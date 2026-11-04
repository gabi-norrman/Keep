"""The search over the signed-in Google accounts' Drives and the read of one Doc or Slides deck, its text cached.

A file id is global, not an account's, so a read tries each account in config order and moves on
when one answers 404 (the file is not shared with it). The text is cached as
`cache/google_drive/<id>-<modified epoch>.txt`: the modified time in the name makes a hit exact
without a sidecar, every page of an unchanged document costs one `files.get` and no export (an
export of a large doc takes Google tens of seconds), and an edit gets a fresh export while the
older files of the id are deleted. A deck is read through the Slides API for its `[slide N]`
markers and a spreadsheet through the Sheets API for every tab under a `[sheet <title>]` line;
each falls back to the Drive export — the slides run together, the first tab alone — when its
API is not enabled in the Cloud project. A fallback result is never cached, and a cached text
without the markers is not served, so enabling the API counts from the next read."""
import csv
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from assistant.integration.Accounts import slug
from common.Paths import CACHE_DIR
from common.logging import error, info, trace
from integrations.google.GoogleAuth import NotSignedIn
from integrations.google.GoogleDrive import (CSV_MIME_TYPE, ApiDisabled, DocHead, DriveFile, GoogleDrive, GoogleDriveError,
                                             file_id_of)

TEXT_CACHE_DIR = CACHE_DIR / "google_drive"
SECTION_MARKERS = ("[slide 1]", "[sheet ")


@dataclass(frozen=True)
class Document:
    account: str
    id: str
    head: DocHead
    text: str
    from_cache: bool

    @property
    def whole(self) -> bool:
        """Whether a deck or a spreadsheet came through its own API — every slide under a
        `[slide N]` line, every tab under a `[sheet <title>]` one — rather than the Drive export."""
        if self.head.is_presentation or self.head.is_spreadsheet:
            return self.text.startswith(SECTION_MARKERS)
        return True


def csv_rows(text: str) -> str:
    """A CSV export as the Sheets API path renders a tab: a row per line, the cells separated by
    tabs, trailing empty cells and empty rows left out."""
    rows = ["\t".join(row).rstrip("\t") for row in csv.reader(io.StringIO(text))]
    return "\n".join(row for row in rows if row.strip())


class DriveFiles:
    """The Drives of the signed-in accounts, read-only, as one."""

    def __init__(self, drives: list[GoogleDrive]):
        self._drives = drives

    def accounts(self) -> list[str]:
        return [d.account for d in self._drives]

    def search(self, words: str, account: str = "") -> tuple[list[tuple[str, DriveFile]], bool]:
        """(account name, file) for every match, newest first per account, and whether more exist."""
        drives = self._named(account)
        found: list[tuple[str, DriveFile]] = []
        more = False
        problems: list[Exception] = []
        for drive in drives:
            try:
                files, has_more = drive.search(words)
            except (NotSignedIn, GoogleDriveError) as e:
                info(f"Google Drive: the search of {drive.account} failed: {e}")
                problems.append(e)
                continue
            found += [(drive.account, f) for f in files]
            more = more or has_more
        if not found and problems:
            raise problems[0]
        info(f"Google Drive: '{words}' matches {len(found)} file(s) over {len(drives)} account(s)" + (", more exist" if more else ""))
        return found, more

    def document(self, ref: str) -> Document:
        """The text of the Doc or deck `ref` names (a link or an id), from the cache when its
        modified time is known. ValueError for a reference that is none, a kind that cannot be
        read, or a file no account can open."""
        file_id = file_id_of(ref)
        if not file_id:
            raise ValueError(f"{ref!r} is not a Google Docs, Slides or Drive link or a file id")
        if not self._drives:
            raise NotSignedIn("no Google account is signed in — google_login first")
        not_signed_in: Optional[NotSignedIn] = None
        for drive in self._drives:
            try:
                head = drive.head(file_id)
            except GoogleDriveError as e:
                if e.status == 404:
                    trace(f"Google Drive: {drive.account} cannot see {file_id}, trying the next account")
                    continue
                raise
            except NotSignedIn as e:
                info(f"Google Drive: {drive.account} skipped — {e}")
                not_signed_in = e
                continue
            if not head.readable:
                info(f"Google Drive: «{head.name}» is a {head.kind}, not read")
                raise ValueError(f"«{head.name}» is a {head.kind}; only a Google Doc, Sheet or Slides deck can be read")
            return self._read(drive, file_id, head)
        if not_signed_in is not None:
            raise not_signed_in
        raise ValueError(f"no signed-in Google account ({', '.join(self.accounts())}) can open {file_id} — is it shared with one of them?")

    def _named(self, account: str) -> list[GoogleDrive]:
        if not account:
            if not self._drives:
                raise NotSignedIn("no Google account is signed in — google_login first")
            return self._drives
        wanted = account.strip().lower()
        drives = [d for d in self._drives if d.account.lower() == wanted]
        if not drives:
            raise ValueError(f"no signed-in Google account named '{account}' (signed in: {', '.join(self.accounts()) or 'none'})")
        return drives

    # --- the text and its cache ------------------------------------------------------------------

    def _read(self, drive: GoogleDrive, file_id: str, head: DocHead) -> Document:
        path = self._cache_path(file_id, head)
        cached = self._cached(path)
        if cached is not None:
            document = Document(drive.account, file_id, head, cached, True)
            if document.whole:
                trace(f"Google Drive: «{head.name}» served from the text cache ({len(cached)} chars)")
                return document
            info(f"Google Drive: the cached text of «{head.name}» is a Drive export without the markers, reading it again")
        if head.is_presentation:
            try:
                text = drive.slides_text(file_id)
            except ApiDisabled as e:
                info(f"Google Drive: the Slides API is not enabled, «{head.name}» is exported with its slides run together — {e}")
                text = drive.export(file_id)
        elif head.is_spreadsheet:
            try:
                text = drive.sheets_text(file_id)
            except ApiDisabled as e:
                info(f"Google Drive: the Sheets API is not enabled, only the first tab of «{head.name}» is exported — {e}")
                text = csv_rows(drive.export(file_id, mime_type=CSV_MIME_TYPE))
        else:
            text = drive.export(file_id)
        document = Document(drive.account, file_id, head, text, False)
        info(f"Google Drive: «{head.name}» ({head.kind}, {drive.account}) read, {len(text):,} chars")
        if document.whole:
            self._cache(path, file_id, text)
        else:
            trace(f"Google Drive: the export of «{head.name}» is not cached, so the API is tried on the next read")
        return document

    @staticmethod
    def _cache_path(file_id: str, head: DocHead) -> Path:
        return TEXT_CACHE_DIR / f"{slug(file_id)}-{int(head.modified_ts)}.txt"

    @staticmethod
    def _cached(path: Path) -> Optional[str]:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            error(f"Google Drive: cache file {path.name} unreadable, exporting again: {e}")
            return None

    @staticmethod
    def _cache(path: Path, file_id: str, text: str) -> None:
        try:
            TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            for old in TEXT_CACHE_DIR.glob(f"{slug(file_id)}-*.txt"):
                if old != path:
                    old.unlink(missing_ok=True)
                    trace(f"Google Drive: older cache file {old.name} removed")
        except OSError as e:
            error(f"Google Drive: could not cache {path.name}: {e}")
