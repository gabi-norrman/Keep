"""Turns a WhatsApp message — a live event or a history-sync row — into the text of a history entry.

Everything here is pure: the protobuf objects are read by attribute, so a test can hand in
plain objects, and the names come from a resolver the integration supplies (the contact
store, the group cache). Nothing in this module imports neonize."""
from dataclasses import dataclass
from typing import Any, Optional, Protocol

SERVER_USER = "s.whatsapp.net"
SERVER_LID = "lid"
SERVER_GROUP = "g.us"
SERVER_BROADCAST = "broadcast"
SERVER_NEWSLETTER = "newsletter"

KIND_IMAGE = "image"
KIND_VIDEO = "video"

# A wrapper carries the real message one level down.
WRAPPERS = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension",
            "documentWithCaptionMessage", "deviceSentMessage", "editedMessage")
PLACEHOLDERS = {
    "stickerMessage": "[Sticker]",
    "locationMessage": "[Location]",
    "liveLocationMessage": "[Live location]",
    "contactMessage": "[Contact card]",
    "contactsArrayMessage": "[Contact cards]",
    "pollCreationMessage": "[Poll]",
    "pollCreationMessageV3": "[Poll]",
}


@dataclass
class Media:
    kind: str               # KIND_IMAGE / KIND_VIDEO
    mimetype: str
    message: Any            # the E2E Message the media belongs to, what `download_any` takes


@dataclass
class Content:
    text: str = ""
    media: Optional[Media] = None

    @property
    def empty(self) -> bool:
        return not self.text and self.media is None


@dataclass
class Incoming:
    """One message as the feed sees it, whichever way it arrived."""
    timestamp: float
    chat: str               # the chat's JID, "4670…@s.whatsapp.net" or "1234-5678@g.us"
    sender: str             # the sender's JID (the chat's in a direct chat), "" when unknown
    from_me: bool
    push_name: str          # the name the sender set for themselves, "" when the message carries none
    message: Any            # the E2E Message protobuf
    from_history: bool      # a history-sync row, never analysed (its media is long gone from the servers)
    edit: str = ""          # non-empty for an edit of an earlier message


class Names(Protocol):
    def person(self, jid: str, push_name: str) -> str: ...
    def group(self, jid: str) -> str: ...


def jid_parts(jid: str) -> tuple[str, str]:
    """("4670…", "s.whatsapp.net") from a JID string; the device suffix (":12") is dropped."""
    user, _, server = jid.partition("@")
    return user.partition(":")[0].partition(".")[0], server


def is_group(jid: str) -> bool:
    return jid_parts(jid)[1] == SERVER_GROUP


def is_skipped_chat(jid: str) -> bool:
    """Status updates and channels are not conversations."""
    return jid_parts(jid)[1] in (SERVER_BROADCAST, SERVER_NEWSLETTER)


def phone_of(jid: str) -> Optional[str]:
    """The E.164 number a phone-number JID stands for, None for a hidden (lid) or group one."""
    user, server = jid_parts(jid)
    return f"+{user}" if server == SERVER_USER and user.isdigit() else None


def _has(message, field: str) -> bool:
    """Whether the sub-message *field* is set: protobuf's HasField, or for a plain object
    (a test's) whether the attribute is there and not empty."""
    has_field = getattr(message, "HasField", None)
    if has_field is not None:
        try:
            return bool(has_field(field))
        except ValueError:      # not a field of this protobuf
            return False
    value = getattr(message, field, None)
    return value is not None and value != ""


def unwrap(message):
    """The message under its wrappers (disappearing, view-once, a document with a caption)."""
    for _ in range(4):
        for wrapper in WRAPPERS:
            if _has(message, wrapper):
                inner = getattr(getattr(message, wrapper), "message", None)
                if inner is not None:
                    message = inner
                    break
        else:
            return message
    return message


def content_of(message) -> Content:
    """What the message says: its text (or caption), and its photo / video when it has one.
    Empty for a protocol message (a revoke, a key exchange), a reaction, a receipt."""
    message = unwrap(message)
    if getattr(message, "conversation", ""):
        return Content(text=message.conversation)
    if _has(message, "extendedTextMessage"):
        return Content(text=getattr(message.extendedTextMessage, "text", "") or "")
    if _has(message, "imageMessage"):
        image = message.imageMessage
        return Content(text=getattr(image, "caption", "") or "",
                       media=Media(KIND_IMAGE, getattr(image, "mimetype", "") or "image/jpeg", message))
    if _has(message, "videoMessage"):
        video = message.videoMessage
        return Content(text=getattr(video, "caption", "") or "",
                       media=Media(KIND_VIDEO, getattr(video, "mimetype", "") or "video/mp4", message))
    if _has(message, "audioMessage"):
        audio = message.audioMessage
        seconds = getattr(audio, "seconds", 0) or 0
        label = "Voice note" if getattr(audio, "PTT", False) else "Audio"
        return Content(text=f"[{label}, {seconds} s]" if seconds else f"[{label}]")
    if _has(message, "documentMessage"):
        document = message.documentMessage
        name = getattr(document, "fileName", "") or getattr(document, "title", "") or ""
        return Content(text=f"[Document: {name}]" if name else "[Document]")
    for field, placeholder in PLACEHOLDERS.items():
        if _has(message, field):
            return Content(text=placeholder)
    return Content()


def placeholder(kind: str) -> str:
    return "[Video]" if kind == KIND_VIDEO else "[Photo]"


def format_entry(incoming: Incoming, names: Names, owner: str, content: Content, media_text: str = "") -> str:
    """The history line, in the shape the Telegram feed writes: who, to whom, what.
    *media_text* is the analyser's description (or a placeholder) of `content.media`."""
    group = is_group(incoming.chat)
    text = content.text
    if media_text and text:
        body = f"{media_text}\nText message: {text}"
    else:
        body = media_text or text
    if incoming.from_me:
        if group:
            target = names.group(incoming.chat)
        elif incoming.sender and jid_parts(incoming.chat)[0] == jid_parts(incoming.sender)[0]:
            target = f"{owner} (note to self)"
        else:
            target = names.person(incoming.chat, "")
        return f"WhatsApp sent:\n{owner} to {target}: {body}"
    sender = names.person(incoming.sender or incoming.chat, incoming.push_name)
    target = names.group(incoming.chat) if group else owner
    return f"WhatsApp received:\nFrom {sender} to {target}: {body}"
