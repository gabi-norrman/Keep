"""The frames a streaming chat draft shows before it has any text, and the test that tells such a
placeholder from a message.

Keep's Telegram transport posts a reply bubble as a spinner frame the moment a turn starts and
edits the text into it as it streams. A feed reading a group through the owner's account gets
that bubble as a new message — the first frame, never the edit — so another Keep's draft in a
shared group would read as a message of one `█`. Both sides use this module: the transport
draws its frames and phase labels from it, the feed recognises them (the labels in Telegram's
HTML or in the Markdown Telethon renders a message's text as).
"""
import re

SPINNER_FRAMES_C64 = ("█", "░")
SPINNER_FRAMES_BRAILLE = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SPINNER_FRAME_CHARS = frozenset(SPINNER_FRAMES_C64 + SPINNER_FRAMES_BRAILLE)

# The line a draft puts over its frame while it waits for something in particular; a detail
# in parentheses may follow ("Thinking (↓ 1.2k tokens · 45 t/s)…", "Prefilling (43%)…").
THINKING_LABEL = "Thinking"
PREFILL_LABEL = "Prefilling"
COMMAND_LABEL = "Running command"
COMMANDS_LABEL = "Running commands"
PHASE_LABELS = (THINKING_LABEL, PREFILL_LABEL, COMMAND_LABEL, COMMANDS_LABEL)

_MARKUP = re.compile(r"</?code>|`")
_PHASE_LINE = re.compile(r"^(?:" + "|".join(re.escape(label) for label in PHASE_LABELS) + r")(?: \([^)]*\))?…$")


def is_draft_placeholder(text: str) -> bool:
    """True when *text* is nothing but a draft's placeholder: spinner frames, with or without a
    phase line over them, in the HTML the transport sends or the Markdown Telethon reads back."""
    if not text:
        return False
    lines = [line.strip() for line in _MARKUP.sub("", text).splitlines() if line.strip()]
    if not lines:
        return False
    *labels, frames = lines
    if not all(ch in SPINNER_FRAME_CHARS or ch.isspace() for ch in frames):
        return False
    return all(_PHASE_LINE.match(label) for label in labels)
