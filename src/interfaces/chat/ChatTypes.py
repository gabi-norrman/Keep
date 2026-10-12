"""Plain data crossing the chat contract in both directions."""
from dataclasses import dataclass, field
from typing import Any, Optional

KIND_TEXT = "text"
KIND_PHOTO = "photo"
KIND_VOICE = "voice"
KIND_VIDEO = "video"
KIND_REACTION = "reaction"


@dataclass
class Attachment:
    """A file the user sent, already downloaded by the transport."""
    data: bytes
    mime_type: str
    # "photo" | "voice" | "video" | "video_note" | "animation" — drives the intake and the
    # wording the assistant sees ("[User sent a video note] …").
    kind: str = KIND_PHOTO

    @property
    def label(self) -> str:
        return {"video_note": "video note", "animation": "GIF"}.get(self.kind, self.kind)


@dataclass
class IncomingMessage:
    """One thing the user did in a chat, normalised by the transport: authorised, files
    downloaded, reply/reaction targets resolved in the platform's own id space."""
    conversation: Any                     # transport-scoped id (Telegram chat_id, …)
    sender: str                           # display name → RequestContext.user
    message_id: Any = None
    kind: str = KIND_TEXT
    text: str = ""                        # message text, or the caption of a media message
    attachments: list = field(default_factory=list)
    reply_to_text: Optional[str] = None   # quoted text when the user replied to a message
    reaction: Optional[str] = None        # emoji(s) the user added
    reaction_target_text: Optional[str] = None
    group_id: Optional[str] = None        # album / media-group id: photos sharing it become one request
    chat_name: Optional[str] = None       # a group's title → RequestContext.location; None in a private chat
    guest: bool = False                   # the sender is not one of the household's owners
    sender_is_bot: bool = False           # the sender is another assistant (a Telegram bot)


@dataclass(frozen=True)
class Progress:
    """What the assistant is doing while nothing user-visible has been produced yet."""
    phase: str                  # "waiting" | "prefill" | "thinking" | "commands"
    thinking_text: str = ""     # the <thinking> block so far (the TUI shows it; Telegram does not)
    output_tokens: int = 0      # running output-token estimate ("Thinking (↓ 1.2k tokens)…")
    elapsed: float = 0.0        # seconds in the current phase (since the LLM call went out,
                                # or since the running command started)
    tick: int = 0               # how many progress updates preceded this one
    commands: int = 0           # commands running right now (phase "commands")
    prefill: float = 0.0        # prompt-prefill completion 0-100 (phase "prefill")


PHASE_WAITING = "waiting"       # an LLM call is out and nothing has come back yet
PHASE_PREFILL = "prefill"       # a local model is still prefilling the prompt (no token yet)
PHASE_THINKING = "thinking"     # the LLM is in its <thinking> block
PHASE_COMMANDS = "commands"     # a command the LLM asked for is running

DECISION_APPROVE = "approve"
DECISION_DENY = "deny"
DECISION_TIMEOUT = "timeout"
DECISION_UNAVAILABLE = "unavailable"

# Secret requests (a value typed into a masked prompt, never seen by the LLM).
DECISION_ACCEPTED = "accepted"
DECISION_CANCEL = "cancel"
DECISION_REJECTED = "rejected"   # every attempt was refused by the requester's `accept`
