"""A MailAccount over Microsoft Graph: the mailbox of a Microsoft 365 account read, searched, filed and sent through the REST API.

Graph's message ids are global — not folder-scoped like IMAP UIDs — so the base's folder
scoping is ignored here: a short id maps straight to a message id. The folder names the LLM
uses are resolved through `/me/mailFolders` (child folders as "Parent/Child"); the well-known
ones (Inbox, Sent Items, Drafts, Deleted Items, Junk Email, Archive) need no lookup. A send
goes through `/me/sendMail`, which files the Sent copy itself; the body is taken from the
`email.message.Message` the base builds (plain text, or the HTML alternative of the nightly
report). A message or attachment is adapted to the attribute shape the base renders
(`GraphMessage`, `GraphAttachment`). The errors the LLM reads: a lost token says to run
microsoft_login, anything else carries Graph's own short message."""
import base64
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Callable, List, Optional

from assistant.integration.mail.MailAccount import FolderNotFound, MailAccount
from common.logging import error, info, trace
from integrations.microsoft.GraphClient import GraphClient, GraphError
from integrations.microsoft.MicrosoftAuth import NotSignedIn

LIST_SELECT = "id,subject,from,receivedDateTime,isRead"
READ_SELECT = f"{LIST_SELECT},toRecipients,body,hasAttachments"
# Folder names Graph resolves itself, by the name the user says.
WELL_KNOWN_FOLDERS = {"inbox": "inbox", "sent items": "sentitems", "sent": "sentitems", "drafts": "drafts",
                      "deleted items": "deleteditems", "trash": "deleteditems", "junk email": "junkemail",
                      "junk": "junkemail", "archive": "archive"}
_FRACTION_RE = re.compile(r"\.\d+")


def parse_graph_time(value: Optional[str]) -> Optional[datetime]:
    """Graph's `2024-05-01T10:00:00Z` (sometimes with seven fraction digits) as an aware datetime."""
    if not value:
        return None
    text = _FRACTION_RE.sub("", value.strip()).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        trace(f"Graph: unparseable time {value!r}")
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _address(recipient: Optional[dict]) -> str:
    """`Name <address>` of a Graph recipient, the address alone when it has no name."""
    email = (recipient or {}).get("emailAddress") or {}
    address, name = str(email.get("address") or ""), str(email.get("name") or "")
    if name and address and name != address:
        return f"{name} <{address}>"
    return address or name


class GraphAttachment:
    """A fileAttachment in the shape the base's attachment handling reads."""

    def __init__(self, item: dict):
        self.filename = str(item.get("name") or "")
        self.content_type = str(item.get("contentType") or "")
        self.size = int(item.get("size") or 0)
        self.content_id = str(item.get("contentId") or "") or None
        self.content_disposition = "inline" if item.get("isInline") else "attachment"
        self._content_bytes = item.get("contentBytes")

    @property
    def payload(self) -> bytes:
        return base64.b64decode(self._content_bytes) if self._content_bytes else b""


class GraphMessage:
    """A Graph message in the shape the base's renderers read."""

    def __init__(self, item: dict, attachments: Optional[list] = None):
        self.uid = str(item.get("id") or "")
        self.subject = str(item.get("subject") or "")
        self.from_ = _address(item.get("from"))
        self.to = [_address(r) for r in item.get("toRecipients") or []]
        self.date = parse_graph_time(item.get("receivedDateTime"))
        self.flags = ("\\Seen",) if item.get("isRead") else ()
        body = item.get("body") or {}
        content = str(body.get("content") or "")
        if str(body.get("contentType") or "").lower() == "html":
            self.text, self.html = "", content
        else:
            self.text, self.html = content, ""
        self.attachments = attachments or []


def message_body(msg: Message) -> tuple[str, str]:
    """The body of a prepared message as Graph takes it: `(contentType, content)` — the HTML
    alternative when the message has one, the plain text otherwise."""
    html, text = "", ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/html" and not html:
            html = decoded
        elif part.get_content_type() == "text/plain" and not text:
            text = decoded
    return ("HTML", html) if html else ("Text", text)


class GraphMailAccount(MailAccount):
    default_folder = "Inbox"

    def __init__(self, name: str, id_prefix: str, address: Callable[[], str], client: GraphClient):
        super().__init__()
        self._name = name
        self._id_prefix = id_prefix
        self._address = address
        self._client = client
        self._folder_ids: dict[str, str] = {}
        self.AUTH_HELP = f'sign in again with microsoft_login("{name}")'

    @property
    def name(self) -> str:
        return self._name

    @property
    def id_prefix(self) -> str:
        return self._id_prefix

    @property
    def address(self) -> str:
        return self._address() or ""

    # --- folders -----------------------------------------------------------------------------

    def _folders(self) -> List[str]:
        """Every folder by display name, a child as "Parent/Child" however deep the nesting goes;
        the ids are kept for the lookups that follow."""
        found: dict[str, str] = {}
        params = {"$top": 200, "$select": "id,displayName,childFolderCount"}

        def walk(url: str, prefix: str):
            for folder in self._client.get_all(url, params):
                name = prefix + str(folder.get("displayName") or "")
                found[name] = folder["id"]
                if int(folder.get("childFolderCount") or 0) > 0:
                    walk(f"/me/mailFolders/{folder['id']}/childFolders", f"{name}/")

        walk("/me/mailFolders", "")
        self._folder_ids = {name.lower(): folder_id for name, folder_id in found.items()}
        trace(f"{self.name}: {len(found)} mail folders")
        return list(found)

    def _folder_id(self, folder: str) -> str:
        key = folder.strip().lower()
        if key in WELL_KNOWN_FOLDERS:
            return WELL_KNOWN_FOLDERS[key]
        if key not in self._folder_ids:
            self._folders()
        if key not in self._folder_ids:
            raise FolderNotFound(f"{self.name} has no folder '{folder}'")
        return self._folder_ids[key]

    # --- the primitives ----------------------------------------------------------------------

    def _list(self, folder: str, unread_only: bool) -> list:
        params = {"$top": self.MAX_RESULTS, "$orderby": "receivedDateTime desc", "$select": LIST_SELECT}
        if unread_only:
            # Graph rejects $orderby=receivedDateTime beside a filter that does not name the
            # property first (InefficientFilter): an always-true date predicate leads.
            params["$filter"] = "receivedDateTime ge 1970-01-01T00:00:00Z and isRead eq false"
        items = self._client.get(f"/me/mailFolders/{self._folder_id(folder)}/messages", params).get("value", [])
        return [GraphMessage(item) for item in items]

    def _search(self, search_string: str) -> list:
        # Graph refuses $orderby next to $search: the hits come by relevance.
        escaped = search_string.replace("\\", "\\\\").replace('"', '\\"')
        params = {"$search": f'"{escaped}"', "$top": self.MAX_RESULTS, "$select": LIST_SELECT}
        items = self._client.get("/me/messages", params).get("value", [])
        return [GraphMessage(item) for item in items]

    def _fetch(self, uid: str, folder: str):
        try:
            item = self._client.get(f"/me/messages/{uid}", {"$select": READ_SELECT})
        except GraphError as e:
            if e.status == 404:
                return None
            raise
        attachments = []
        if item.get("hasAttachments"):
            for att in self._client.get_all(f"/me/messages/{uid}/attachments"):
                if att.get("@odata.type", "").endswith("fileAttachment"):
                    attachments.append(GraphAttachment(att))
                else:
                    trace(f"{self.name}: attachment '{att.get('name')}' is {att.get('@odata.type')}, skipped")
        return GraphMessage(item, attachments)

    def _move(self, uid: str, source: str, dest_folder: str) -> None:
        self._client.post(f"/me/messages/{uid}/move", {"destinationId": self._folder_id(dest_folder)})

    def _recent_from(self, sender_contains: str, limit: int) -> list:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime desc",
                  "$top": 50, "$select": f"{LIST_SELECT},body"}
        items = self._client.get("/me/messages", params).get("value", [])
        wanted = sender_contains.lower()
        matches = [GraphMessage(item) for item in items if wanted in _address(item.get("from")).lower()]
        trace(f"{self.name}: {len(matches)} of {len(items)} messages of the last day are from '{sender_contains}'")
        return matches[:limit]

    def _send(self, msg: Message, recipients: List[str]) -> str:
        content_type, content = message_body(msg)
        payload = {
            "message": {
                "subject": str(msg.get("Subject", "")),
                "body": {"contentType": content_type, "content": content},
                "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
            },
            "saveToSentItems": True,
        }
        try:
            self._client.post("/me/sendMail", payload)
        except NotSignedIn as e:
            error(f"Email send: {self.name} — {e}")
            return f"{self.name} {e}"
        except GraphError as e:
            error(f"Email send: {self.name} Graph error: {e}")
            return f"{self.name} send failed: {e}"
        except OSError as e:
            error(f"Email send network error: {e}")
            return f"network error sending email: {e}"
        return ""

    def check(self) -> bool:
        me = self._client.get("/me", {"$select": "userPrincipalName,mail"})
        info(f"{self.name}: Graph reachable as {me.get('mail') or me.get('userPrincipalName') or 'an account'}")
        return True
