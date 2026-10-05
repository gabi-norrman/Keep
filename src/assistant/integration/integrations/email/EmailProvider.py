import re
import smtplib
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, date
from email.message import Message
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Dict, List, Optional

from imap_tools import MailBox, AND, MailMessageFlags
from imap_tools.errors import MailboxFolderSelectError

from common.logging import trace, info, error
from common.RateLimiter import RateLimiter
from ....tools import PdfExtractor
from ....tools.MediaAnalyzer import media_analyzer, media_kind, KIND_VIDEO


MIN_MEDIA_SIZE = 10 * 1024
SMTP_TIMEOUT = 20
SEND_RATE_LIMIT_WINDOW = 60.0
SEND_RATE_LIMIT_MAX = 5
EMAIL_ADDRESS_PATTERN = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def _is_junk_media(att) -> bool:
    """Tiny files and inline (cid-referenced) images are signatures, logos and tracking pixels;
    anything Floxy cannot describe (svg, audio, ...) is skipped too."""
    size = att.size or 0
    if size < MIN_MEDIA_SIZE:
        return True
    if att.content_disposition == "inline" and att.content_id:
        return True
    return media_kind(att.content_type or "", att.filename) is None


def _analyze_media(payload: bytes, content_type: str, filename: str) -> str:
    label = "Video" if media_kind(content_type, filename) == KIND_VIDEO else "Image"
    return media_analyzer().describe_bytes(
        payload, mime_type=content_type, filename=filename,
        context=f"{label} attached to an email, filename: {filename}", fallback=f"[{label}]")


class EmailProvider(ABC):
    """Base class that lists, searches, reads, moves and sends mail for one account.

    Sending goes over SMTP and then files a copy in the account's Sent folder over IMAP
    (`SENT_FOLDER`), so what the assistant sends shows up in the user's mail client like
    anything they sent themselves. A provider whose server files SMTP sends on its own
    leaves `SENT_FOLDER` as None.

    Emails are exposed to the LLM through short ids (prefix + counter) that are cached
    per listing; IMAP UIDs are folder-scoped, so the cache remembers its source folder.
    """

    MAX_RESULTS = 35
    MAX_BODY_LENGTH = 8000
    # imap_tools defaults to no socket timeout — Gmail IMAP has been observed
    # hanging for minutes on connect/login, blocking the bg command worker.
    # 60s is the per-recv socket timeout, generous enough for server-side
    # SEARCH on a large mailbox while still bailing out on a true hang.
    IMAP_TIMEOUT = 60

    def __init__(self):
        self._email_id_cache: Dict[str, str] = {}
        # Subjects keyed by the same short id, populated alongside the id-cache so
        # the read-email progress notification can name the email instead of
        # leaking the internal short id. Kept separate from _email_id_cache so the
        # UID-resolution path (_resolve_email_id/move) stays untouched.
        self._email_subject_cache: Dict[str, str] = {}
        self._next_cache_id: int = 0
        # The folder the current id-cache was built from. IMAP UIDs are
        # folder-scoped, so move() must re-select this exact folder to resolve a
        # short id back to a live UID. Defaults to the standard listing folder.
        self._cache_folder: Optional[str] = None
        # Listings can run concurrently (background commands of one reply run in
        # parallel); rebuilding the id-cache is a multi-step write, so it happens
        # under this lock and the readers take it too. The last listing to finish wins.
        self._cache_lock = threading.Lock()
        self._rate_limiter = RateLimiter(SEND_RATE_LIMIT_MAX, SEND_RATE_LIMIT_WINDOW)

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def id_prefix(self) -> str: ...

    IMAP_HOST: str
    IMAP_FOLDER: str
    SMTP_HOST: str
    SMTP_PORT: int = 587
    # Where a sent copy is filed; None when the server does that itself.
    SENT_FOLDER: Optional[str] = None
    AUTH_HELP = "check the account password"

    @abstractmethod
    def _get_credentials(self) -> tuple[str, str]: ...

    def send(self, to: str, subject: str, body: str) -> str:
        """Sends a plain-text email. Returns "" on success or a short error string on
        failure (the convention the LLM relies on to detect and surface send failures)."""
        recipient = (to or "").strip()
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not recipient:
            info("Email send skipped: empty recipient")
            return "empty recipient"
        if not subject:
            info("Email send skipped: empty subject")
            return "empty subject"
        if not body:
            info("Email send skipped: empty body")
            return "empty body"
        if not EMAIL_ADDRESS_PATTERN.match(recipient):
            info(f"Email send blocked: invalid recipient '{recipient}'")
            return f"invalid recipient: {recipient}"

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["To"] = recipient
        return self.send_message(msg, [recipient])

    def send_message(self, msg: Message, recipients: List[str]) -> str:
        """Sends a prepared message over SMTP, then files a copy in the Sent folder.
        Fills in From, Date and Message-ID when the caller left them out. Returns "" on
        success or a short error string; a failed Sent copy is logged but is not a failure,
        the mail is already out."""
        rate_err = self._rate_limiter.check()
        if rate_err:
            info(f"Email send blocked: {rate_err}")
            return rate_err

        try:
            address, password = self._get_credentials()
        except KeyError as e:
            error(f"Email send: {self.name} credentials not configured: {e}")
            return f"{self.name} credentials not configured"
        if not address or not password:
            error(f"Email send: {self.name} credentials not configured")
            return f"{self.name} credentials not configured"

        if "From" not in msg:
            msg["From"] = address
        if "Date" not in msg:
            msg["Date"] = formatdate(localtime=True)
        if "Message-ID" not in msg:
            msg["Message-ID"] = make_msgid(domain=address.rsplit("@", 1)[-1])

        subject = msg.get("Subject", "")
        try:
            with smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
                server.starttls()
                server.login(address, password)
                server.sendmail(address, list(recipients), msg.as_string())
        except smtplib.SMTPAuthenticationError as e:
            error(f"Email send auth failed: {e}")
            return f"{self.name} SMTP auth failed — {self.AUTH_HELP}"
        except smtplib.SMTPRecipientsRefused as e:
            error(f"Email send: recipient refused: {e}")
            return f"recipient refused: {', '.join(recipients)}"
        except smtplib.SMTPException as e:
            error(f"Email send SMTP error: {e}")
            return f"SMTP error: {e}"
        except (TimeoutError, OSError) as e:
            error(f"Email send network error: {e}")
            return f"network error sending email: {e}"

        self._rate_limiter.record()
        info(f"Email sent from {self.name} to {', '.join(recipients)}: subject={subject!r}")
        self._save_to_sent(msg, address, password)
        return ""

    def _save_to_sent(self, msg: Message, address: str, password: str) -> bool:
        if not self.SENT_FOLDER:
            trace(f"{self.name}: the server files sent mail itself — no Sent copy to save")
            return True
        try:
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password) as mailbox:
                mailbox.append(msg.as_bytes(), self.SENT_FOLDER, flag_set=[MailMessageFlags.SEEN])
            info(f"{self.name}: copy of {msg.get('Subject', '')!r} filed in '{self.SENT_FOLDER}'")
            return True
        except Exception as e:
            error(f"{self.name}: the mail went out but filing a copy in '{self.SENT_FOLDER}' failed: {e}")
            return False

    def fetch_latest(self, folder=None, unread_only=False) -> str:
        # `folder`, when a non-empty string, names an IMAP folder/label to list
        # (e.g. "@SaneLater") instead of the default mailbox. This lets the
        # assistant browse a specific folder before moving messages out of it.
        # `unread_only` restricts the listing to unseen messages (IMAP UNSEEN,
        # portable across Gmail and iCloud) — handy for sweeping a backlog like
        # @SaneLater without re-listing mail that's already been dealt with.
        folder = (str(folder).strip() if folder else "") or self.IMAP_FOLDER
        try:
            scope = "unread " if unread_only else ""
            info(f"Fetching latest {scope}emails from {self.name} ({folder})...")
            address, password = self._get_credentials()
            fetch_kwargs = dict(limit=self.MAX_RESULTS, reverse=True, mark_seen=False, headers_only=True)
            if unread_only:
                fetch_kwargs["criteria"] = AND(seen=False)
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=folder) as mailbox:
                messages = self._timed_fetch(mailbox, "fetch latest", **fetch_kwargs)
            self._cache_folder = folder
            return self._format_email_list(messages)
        except MailboxFolderSelectError:
            info(f"{self.name}: folder '{folder}' does not exist here — no emails.")
            return "No emails found."
        except Exception as e:
            error(f"Failed to fetch latest {self.name} emails: {e}")
            return f"Error fetching {self.name} emails: {e}"

    def search(self, search_string) -> str:
        try:
            info(f"Searching {self.name} for: {search_string}")
            address, password = self._get_credentials()
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=self.IMAP_FOLDER) as mailbox:
                criteria = self._build_search_criteria(search_string)
                # No date filter: server-side SEARCH is index-backed (Gmail) or
                # fast enough (iCloud), and the fetch is capped at MAX_RESULTS
                # newest matches anyway. headers_only keeps the capped fetch
                # cheap — the list view never needs bodies; read() re-fetches.
                messages = self._timed_fetch(mailbox, f"search '{search_string}'",
                    criteria=criteria,
                    charset='UTF-8',
                    limit=self.MAX_RESULTS,
                    reverse=True,
                    mark_seen=False,
                    headers_only=True
                )
            self._cache_folder = self.IMAP_FOLDER
            return self._format_email_list(messages)
        except Exception as e:
            error(f"Failed to search {self.name} emails: {e}")
            return f"Error searching {self.name} emails: {e}"

    def _build_search_criteria(self, search_string):
        return AND(text=search_string)

    def read(self, email_id) -> str:
        try:
            email_id = str(email_id).strip()
            email_id = self._resolve_email_id(email_id)
            # UIDs are scoped to the folder the id-cache was listed from (see move()),
            # so select that folder — reading from INBOX after listing @SaneLater would
            # resolve the UID against the wrong mailbox.
            folder = self._cache_folder or self.IMAP_FOLDER
            info(f"Reading {self.name} email UID: {email_id} from '{folder}'")
            address, password = self._get_credentials()
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=folder) as mailbox:
                messages = self._timed_fetch(mailbox, f"read UID {email_id}",
                    criteria=AND(uid=email_id),
                    mark_seen=False
                )
            if not messages:
                return f"Email with ID {email_id} not found."
            return self._format_email_body(messages[0])
        except Exception as e:
            error(f"Failed to read {self.name} email {email_id}: {e}")
            return f"Error reading email: {e}"

    def list_folders(self, params=None) -> str:
        """List every IMAP folder/label so the assistant can pick a valid
        destination for move() instead of guessing label names."""
        try:
            info(f"Listing {self.name} folders...")
            address, password = self._get_credentials()
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password) as mailbox:
                folders = [f.name for f in mailbox.folder.list()]
            return "\n".join(folders) if folders else "No folders found."
        except Exception as e:
            error(f"Failed to list {self.name} folders: {e}")
            return f"Error listing {self.name} folders: {e}"

    def move(self, email_id, dest_folder) -> str:
        """Move a previously-listed email to another folder/label.

        IMAP UIDs are scoped to the folder they were listed from, so we re-select
        `self._cache_folder` (the folder the id-cache was built from) to resolve
        the short id, then MOVE the message to `dest_folder`. On Gmail this maps
        to relabelling — the source label is removed and `dest_folder` added — so
        it is reversible. Moving out of "[Gmail]/All Mail" is the exception: it
        trashes the message, so the assistant should list a specific folder
        (e.g. "@SaneLater") before moving rather than moving from a search.
        """
        try:
            email_id = str(email_id).strip()
            dest_folder = str(dest_folder).strip().strip('"').strip("'")
            uid = self._resolve_email_id(email_id)
            source = self._cache_folder or self.IMAP_FOLDER
            info(f"Moving {self.name} email UID {uid}: '{source}' -> '{dest_folder}'")
            address, password = self._get_credentials()
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=source) as mailbox:
                mailbox.move(uid, dest_folder)
            # Echo the subject back into the result so the model's feedback channel
            # carries the human-meaningful identity, not just the opaque short id —
            # otherwise it can't tell which message it actually moved. Truncated the
            # same way as the Telegram progress notification.
            subject = self.subject_for(email_id)
            if subject and subject.strip() and subject.strip() != "(no subject)":
                s = subject.strip()
                if len(s) > 50:
                    s = s[:50].rstrip() + "…"
                return f'Moved email {email_id} ("{s}") to {dest_folder}.'
            return f"Moved email {email_id} to {dest_folder}."
        except MailboxFolderSelectError as e:
            error(f"Failed to move {self.name} email {email_id}: folder error: {e}")
            return f"Error moving email: folder not found ({e})"
        except Exception as e:
            error(f"Failed to move {self.name} email {email_id}: {e}")
            return f"Error moving email: {e}"

    def fetch_recent_raw(self, sender_contains: str, since_minutes: int = 10, limit: int = 5) -> List:
        """Fetch recent raw MailMessage objects filtered by sender substring and recency."""
        try:
            address, password = self._get_credentials()
            since_date = date.today() - timedelta(days=1)
            with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=self.IMAP_FOLDER) as mailbox:
                messages = self._timed_fetch(
                    mailbox, f"raw fetch sender~'{sender_contains}'",
                    criteria=AND(from_=sender_contains, date_gte=since_date),
                    limit=limit,
                    reverse=True,
                    mark_seen=False,
                )
            if not messages:
                return []
            tz = messages[0].date.tzinfo if messages[0].date else None
            cutoff = datetime.now(tz) - timedelta(minutes=since_minutes)
            return [m for m in messages if m.date and m.date >= cutoff]
        except Exception as e:
            error(f"Failed to fetch raw {self.name} emails for sender '{sender_contains}': {e}")
            return []

    def _generate_short_id(self) -> str:
        id_num = self._next_cache_id
        self._next_cache_id = (self._next_cache_id + 1) % (26 * 26)
        first = chr(65 + (id_num // 26))
        second = chr(65 + (id_num % 26))
        return f"{first}{second}"

    def _resolve_email_id(self, email_id: str) -> str:
        prefix = f"{self.id_prefix}-"
        if email_id.startswith(prefix):
            email_id = email_id[len(prefix):]
        with self._cache_lock:
            return self._email_id_cache.get(email_id, email_id)

    def subject_for(self, email_id: str) -> Optional[str]:
        """Cached subject for a short id from the most recent listing, or None on
        a miss (id never listed, cache cleared since). Used only for the friendly
        read-email notification, so a miss is harmless."""
        short_id = str(email_id).strip()
        prefix = f"{self.id_prefix}-"
        if short_id.startswith(prefix):
            short_id = short_id[len(prefix):]
        with self._cache_lock:
            return self._email_subject_cache.get(short_id)

    def _timed_fetch(self, mailbox, description, **kwargs):
        trace(f"  {self.name}: IMAP query ({description})...")
        start = time.time()
        messages = list(mailbox.fetch(**kwargs))
        trace(f"  {self.name}: got {len(messages)} results in {time.time() - start:.1f}s")
        return messages

    @staticmethod
    def _format_date(dt: datetime) -> str:
        if not dt:
            return "unknown"
        # Header dates are tz-aware (usually UTC or the sender's tz); show them
        # in the server's local time so the LLM reasons in the user's clock.
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%b %d %H:%M")

    def _format_email_list(self, messages: list) -> str:
        if not messages:
            return "No emails found."
        lines = []
        with self._cache_lock:
            self._email_id_cache.clear()
            self._email_subject_cache.clear()
            self._next_cache_id = 0
            for msg in messages:
                short_id = self._generate_short_id()
                self._email_id_cache[short_id] = msg.uid
                date_str = self._format_date(msg.date)
                from_addr = msg.from_ or "unknown"
                subject = msg.subject or "(no subject)"
                self._email_subject_cache[short_id] = subject
                unread = " [UNREAD]" if "\\Seen" not in (msg.flags or ()) else ""
                lines.append(f"[{self.id_prefix}-{short_id}]{unread} {date_str} | From: {from_addr} | Subject: {subject}")
        return "\n".join(lines)

    @staticmethod
    def strip_html(html: str) -> str:
        from bs4 import BeautifulSoup, NavigableString, Comment

        soup = BeautifulSoup(html, 'html.parser')
        for element in soup(['script', 'style', 'head']):
            element.decompose()

        # Preserve anchor links as <a href="...">text</a> instead of flattening
        # to bare text. Transactional emails (booking confirmations, etc.) put
        # cancel/manage actions behind <a> buttons; get_text() would drop the
        # href and leave the assistant with a label and no URL to follow.
        def extract(element) -> str:
            parts = []
            for child in element.children:
                if isinstance(child, Comment):
                    continue
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text:
                        parts.append(text)
                elif child.name == 'a' and child.get('href'):
                    href = child['href']
                    link_text = child.get_text(strip=True)
                    if href.startswith(('http://', 'https://', 'mailto:')):
                        label = link_text or href
                        parts.append(f'<a href="{href}">{label}</a>')
                    elif link_text:
                        parts.append(link_text)
                elif child.name == 'br':
                    parts.append('\n')
                elif child.name in ('p', 'div', 'tr', 'li', 'table', 'section',
                                    'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                    inner = extract(child)
                    if inner:
                        parts.append(inner)
                        parts.append('\n')
                else:
                    inner = extract(child)
                    if inner:
                        parts.append(inner)
            return ' '.join(parts)

        root = soup.find('body') or soup
        text = extract(root)
        lines = [line.strip() for line in text.split('\n')]
        lines = [line for line in lines if line]
        return '\n'.join(lines)

    @staticmethod
    def strip_html_plain(html: str) -> str:
        """Flatten HTML to visible text only, dropping all href URLs.

        Use this when the consumer needs the human-readable text without link
        markup — e.g. regex-scanning for a verification code, where an embedded
        URL containing digits would produce false matches. For email bodies
        shown to the assistant, prefer link-preserving strip_html().
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        for element in soup(['script', 'style', 'head']):
            element.decompose()
        text = soup.get_text('\n', strip=True)
        lines = [line for line in text.splitlines() if line.strip()]
        return '\n'.join(lines)

    @staticmethod
    def _extract_links(html: str) -> List[str]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        for element in soup(['script', 'style', 'head']):
            element.decompose()
        urls = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith(('http://', 'https://')) and href not in seen:
                seen.add(href)
                urls.append(href)
        return urls

    def _format_email_body(self, msg) -> str:
        if msg.text:
            body = msg.text
            # The text/plain part often omits the deep links (cancel/manage
            # booking, etc.) that live only in the HTML alternative. Surface any
            # HTML links not already present in the chosen body.
            if msg.html:
                missing = [u for u in self._extract_links(msg.html) if u not in body]
                if missing:
                    body += "\n\nLinks:\n" + "\n".join(missing)
        elif msg.html:
            body = self.strip_html(msg.html)
        else:
            body = "(no body)"
        if len(body) > self.MAX_BODY_LENGTH:
            body = body[:self.MAX_BODY_LENGTH] + "\n... (truncated)"

        attachments_text = self._process_attachments(msg)

        result = (
            f"From: {msg.from_}\n"
            f"To: {', '.join(msg.to)}\n"
            f"Date: {self._format_date(msg.date)}\n"
            f"Subject: {msg.subject}\n"
            f"\n{body}"
        )
        if attachments_text:
            result += f"\n\n{attachments_text}"
        return result

    @staticmethod
    def _process_attachments(msg) -> str:
        if not msg.attachments:
            return ""

        sections = []
        for att in msg.attachments:
            content_type = att.content_type or ""
            filename = att.filename or "unnamed"
            size = att.size or 0
            size_kb = size / 1024

            if content_type == "application/pdf":
                info(f"Email attachment: extracting PDF '{filename}' ({size_kb:.0f} KB)")
                text = PdfExtractor.extract_text(att.payload)
                sections.append(f"--- Attachment: {filename} (PDF, {size_kb:.0f} KB) ---\n{text}")

            elif content_type.startswith(("image/", "video/")):
                kind = "video" if content_type.startswith("video/") else "image"
                if _is_junk_media(att):
                    trace(f"Email attachment: skipping small/inline/unsupported {kind} '{filename}' ({size_kb:.0f} KB)")
                    continue
                info(f"Email attachment: analyzing {kind} '{filename}' ({size_kb:.0f} KB)")
                description = _analyze_media(att.payload, content_type, filename)
                sections.append(f"--- Attachment: {filename} ({kind}, {size_kb:.0f} KB) ---\n{description}")

            else:
                trace(f"Email attachment: unsupported type '{content_type}' for '{filename}'")

        return "\n\n".join(sections)


class NotConfiguredEmail:
    """What the mail commands run against when no email section is configured: every
    operation answers with the setup hint instead of a KeyError, so the assistant offers
    to set the integration up (plans/SELF_SETUP_PLAN.md §3.5)."""

    HINT = ("Email is not configured (email.address / email.password for Gmail, icloud_email.address / "
            "icloud_email.password for iCloud). Use list_integrations and config_help to set it up.")

    name = "Email"

    def fetch_latest(self, folder=None, unread_only=False) -> str:
        return self.HINT

    def search(self, search_string) -> str:
        return self.HINT

    def read(self, email_id) -> str:
        return self.HINT

    def list_folders(self, params=None) -> str:
        return self.HINT

    def move(self, email_id, dest_folder) -> str:
        return self.HINT

    def subject_for(self, email_id):
        return None
