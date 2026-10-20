"""Reads a stored assistant reply back the way its stream showed it.

The history keeps a reply as the model wrote it — command calls and all, a `save_watch`
with its whole text among them — because that is what the LLM must see on its next turn.
A user watching the stream saw something else: the prose with the calls taken out, and
one echo per call ("Searching for …", `Commands.echo_text`). The reader rebuilds that
view from the stored text, with the parser the stream itself uses, so a chat redrawn from
the history reads like the one that was watched live.
"""
import re
from dataclasses import dataclass

from common.logging import trace

from .formats.CommandFormats import CommandFormats
from .Commands import Commands

WRAP_OPENS = re.compile(r"(`+|<code>)\s*$")
WRAP_CLOSES = re.compile(r"^\s*(`+|</code>)")
CODE_TAGS = ("<code>", "</code>")
BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class StoredReply:
    """A stored reply as its stream showed it."""
    prose: str          # the reply without its command calls
    before: list[str]   # the echoes of the commands called before the prose began
    after: list[str]    # … and of those called once it had


class StoredReplyReader:
    """Splits a stored assistant reply into its prose and the echoes of its command calls."""

    def __init__(self):
        # Never runs a command: describing one needs no processor behind it.
        self._commands = Commands(None)
        self._format = CommandFormats.active()(self._commands.list_commands())

    def read(self, reply: str) -> StoredReply:
        prose, before, after = "", [], []
        remaining = reply or ""
        while remaining:
            start = self._format.find_start(remaining)
            if start < 0:
                break
            call, parsed_rest = self._format.extract(remaining[start:])
            if call is None:
                # A command name and "(" that never closes: the model was talking about it.
                prose += remaining[:start + 1]
                remaining = remaining[start + 1:]
                continue
            rest = self._after_call(remaining[start:], len(parsed_rest or ""))
            lead, remaining = self._unwrap(remaining[:start], rest)
            prose += lead
            echo = self._commands.echo_text(*call[:2])
            if echo:
                (after if prose.strip() else before).append(echo)
            else:
                trace(f"StoredReplyReader: {call[0]} has no echo, nothing to show for it")
        prose = BLANK_RUN.sub("\n\n", prose + remaining).strip()
        return StoredReply(prose, before, after)

    @staticmethod
    def _after_call(text: str, parsed_rest_chars: int) -> str:
        """What follows the call that opens `text`. The parser drops every code tag before it
        looks, so its own remainder has lost the ones the prose after the call needs: only
        its length is used, to find where the call ends in the text as written."""
        call_chars = len(text.replace(CODE_TAGS[0], "").replace(CODE_TAGS[1], "")) - parsed_rest_chars
        position = 0
        while call_chars > 0 and position < len(text):
            tag = next((t for t in CODE_TAGS if text.startswith(t, position)), None)
            if tag:
                position += len(tag)
            else:
                position += 1
                call_chars -= 1
        return text[position:]

    @staticmethod
    def _unwrap(lead: str, rest: str) -> tuple[str, str]:
        """Drop the backticks or code tags a model wrapped a call in; without the call they
        would be left facing each other in the prose."""
        opening = WRAP_OPENS.search(lead)
        closing = WRAP_CLOSES.search(rest)
        if not opening or not closing:
            return lead, rest
        opens, closes = opening.group(1), closing.group(1)
        if (opens, closes) != CODE_TAGS and (opens != closes or not opens.startswith("`")):
            return lead, rest
        return lead[:opening.start()], rest[closing.end():]
