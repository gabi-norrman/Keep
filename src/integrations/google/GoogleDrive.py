"""The Drive API v3 calls the docs feed needs: a document's head, its revisions, and the plain
text of one revision.

The shape of `GoogleCalendarAccount._call`: a bearer token from `GoogleAuth.token()`, one retry
on a rate limit, 401 → `NotSignedIn`, 403 for a missing scope → `NotSignedIn` with the
sign-in-again hint (the token on file was granted before the Drive scope was asked for).
A revision of a native Google Doc carries `exportLinks`, one per format; the `text/plain` one
is what the diff is made of. Google sends that export with a BOM, which `export` strips."""
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

from common.logging import error, info, trace
from integrations.google.GoogleAuth import GoogleAuth, NotSignedIn, sign_in_again_hint

BASE_URL = "https://www.googleapis.com/drive/v3"
DOC_MIME_TYPE = "application/vnd.google-apps.document"
EXPORT_MIME_TYPE = "text/plain"
TIMEOUT = 60
RETRY_AFTER = 1
RATE_LIMIT_REASONS = ("rateLimitExceeded", "userRateLimitExceeded")
SCOPE_REASONS = ("insufficientPermissions", "insufficientFilePermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT")
REVISION_FIELDS = "nextPageToken,revisions(id,modifiedTime,lastModifyingUser/displayName,exportLinks)"
HEAD_FIELDS = "name,mimeType,modifiedTime"
PAGE_SIZE = 1000


class GoogleDriveError(Exception):
    """Drive answered with an error status; `status` carries it."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class DocHead:
    name: str
    mime_type: str
    modified_ts: float

    @property
    def is_document(self) -> bool:
        return self.mime_type == DOC_MIME_TYPE


@dataclass(frozen=True)
class Revision:
    id: str
    modified_ts: float
    author: str
    export_url: Optional[str]     # the text/plain export of this revision; None for an older one without links


def rfc3339_ts(value: str) -> float:
    """`2026-10-14T12:02:33.123Z` → epoch seconds."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class GoogleDrive:
    """One account's Drive, read-only."""

    def __init__(self, account: str, auth: GoogleAuth):
        self.account = account
        self.auth = auth

    def head(self, file_id: str) -> DocHead:
        body = self._call("GET", f"/files/{file_id}", {"fields": HEAD_FIELDS})
        return DocHead(str(body.get("name") or ""), str(body.get("mimeType") or ""), rfc3339_ts(body["modifiedTime"]))

    def revisions(self, file_id: str) -> list[Revision]:
        """Every revision of the file, oldest first."""
        found: list[Revision] = []
        params: dict = {"fields": REVISION_FIELDS, "pageSize": PAGE_SIZE}
        pages = 0
        while True:
            page = self._call("GET", f"/files/{file_id}/revisions", params)
            pages += 1
            for item in page.get("revisions", []):
                links = item.get("exportLinks") or {}
                found.append(Revision(str(item["id"]), rfc3339_ts(item["modifiedTime"]),
                                      str((item.get("lastModifyingUser") or {}).get("displayName") or "someone"),
                                      links.get(EXPORT_MIME_TYPE)))
            if not page.get("nextPageToken"):
                break
            params = {**params, "pageToken": page["nextPageToken"]}
        found.sort(key=lambda r: r.modified_ts)
        trace(f"{self.account}: {len(found)} revisions of {file_id} over {pages} page(s)")
        return found

    def export(self, file_id: str, revision: Optional[Revision] = None) -> str:
        """The plain text of `revision`, or of the document as it is now."""
        if revision is not None and revision.export_url:
            response = self._request("GET", revision.export_url, None)
        else:
            if revision is not None:
                info(f"{self.account}: revision {revision.id} of {file_id} has no export link, exporting the head instead")
            response = self._request("GET", f"{BASE_URL}/files/{file_id}/export", {"mimeType": EXPORT_MIME_TYPE})
        text = response.content.decode("utf-8-sig", errors="replace")
        trace(f"{self.account}: exported {len(text)} chars of {file_id}" + (f" at revision {revision.id}" if revision else ""))
        return text

    # --- the API -----------------------------------------------------------------------------

    def _call(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        response = self._request(method, BASE_URL + path, params)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def _request(self, method: str, url: str, params: Optional[dict], retried: bool = False):
        token = self.auth.token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        started = time.time()
        response = requests.request(method, url, params=params, headers=headers, timeout=TIMEOUT)
        shown = url[len(BASE_URL):] if url.startswith(BASE_URL) else "export link"
        trace(f"{self.account}: {method} {shown} -> {response.status_code} in {time.time() - started:.1f}s")
        if response.status_code == 401:
            info(f"{self.account}: Drive answered 401 — the token is not accepted")
            raise NotSignedIn(f"token not accepted, {sign_in_again_hint(self.account)}")
        reason = self._reason(response) if response.status_code in (403, 429) else ""
        if response.status_code == 403 and reason in SCOPE_REASONS:
            info(f"{self.account}: Drive answered 403 {reason} — the sign-in did not grant the Drive scope")
            raise NotSignedIn(f"signed in without the Drive scope, {sign_in_again_hint(self.account)}")
        if response.status_code == 429 or reason in RATE_LIMIT_REASONS:
            if retried:
                raise GoogleDriveError(response.status_code, "Google is rate-limiting the account, try again in a minute")
            info(f"{self.account}: Drive answered {response.status_code} ({reason or 'too many requests'}), retrying once in {RETRY_AFTER}s")
            time.sleep(RETRY_AFTER)
            return self._request(method, url, params, retried=True)
        if response.status_code >= 400:
            message = self._message(response)
            error(f"{self.account}: Drive {method} {shown} failed: {response.status_code} {message}")
            raise GoogleDriveError(response.status_code, message)
        return response

    @staticmethod
    def _reason(response) -> str:
        try:
            err = response.json().get("error") or {}
            errors = err.get("errors") or []
            if errors and errors[0].get("reason"):
                return str(errors[0]["reason"])
            details = err.get("details") or []
            return str(details[0].get("reason") or "") if details else ""
        except (ValueError, AttributeError, IndexError):
            return ""

    @staticmethod
    def _message(response) -> str:
        try:
            err = response.json().get("error") or {}
            return str(err.get("message") or err.get("status") or response.text)[:300]
        except (ValueError, AttributeError):
            return (response.text or f"HTTP {response.status_code}")[:300]
