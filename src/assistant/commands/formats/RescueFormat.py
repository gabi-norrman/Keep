"""The active command format with Claude's `<invoke>` block read beside it, so a call the model writes in its own trained shape under another syntax still runs.

Opus 5.5 writes a `<function_calls>` / `<invoke>` block now and then whatever syntax the
prompt teaches: four of 428 replies under Keep's syntax on the production server between
2026-10-18 and -21, each a whole, correct block and nothing else (Fable never did it once).
Keep's parser saw no call in them, so the raw XML reached the chat, and the stored turn then
showed the model its own `<invoke` on every later prompt — the loop that makes the drift
recur. This format wraps the active one: a buffer is read by whichever of the two syntaxes
opens a call first in it, a call the invoke syntax read is logged at NOTE level and runs like
any other, and `rewritten` writes every such block back in the active syntax (its
`render_tool_call`, the way `OllamaLLM` writes a server-parsed call) before the turn is
stored, so the history holds nothing but the syntax the prompt teaches. The invoke format
itself needs no rescue and `wrap` hands it back as it is.
"""
from typing import Iterator, Sequence

from common.logging import info, note

from .CommandFormat import CommandFormat
from .InvokeFormat import InvokeFormat
from .Signatures import Declaration, Value


class RescueFormat(CommandFormat):
    """The active format, with the `<invoke>` syntax read as a fallback for the calls the
    model writes in that shape instead; the texts render in the active format alone."""

    def __init__(self, primary: CommandFormat, rescue: InvokeFormat):
        super().__init__(primary.possible_commands)
        self.name = primary.name
        self.primary = primary
        self.rescue = rescue
        self.rescued = 0  # the calls read by the rescue syntax so far (one processor, one reply)

    @classmethod
    def wrap(cls, fmt: CommandFormat) -> CommandFormat:
        """`fmt` with the rescue beside it — or `fmt` itself when it is the invoke format."""
        if isinstance(fmt, InvokeFormat):
            return fmt
        return cls(fmt, InvokeFormat(fmt.possible_commands))

    # --- rendering: the active format's ------------------------------------------------------

    def instructions(self) -> str:
        return self.primary.instructions()

    def render_declaration(self, declaration: Declaration) -> str:
        return self.primary.render_declaration(declaration)

    def render_example(self, name: str, values: Sequence[Value], declaration: Declaration | None) -> str:
        return self.primary.render_example(name, values, declaration)

    def render_tool_call(self, name: str, arguments) -> str:
        return self.primary.render_tool_call(name, arguments)

    # --- the stream: whichever syntax opens a call first reads it -----------------------------

    def _reader(self, buffer: str) -> CommandFormat:
        """The format whose call starts earliest in the buffer; the active one on a tie and
        when neither opens a call."""
        primary, rescue = self.primary.find_start(buffer), self.rescue.find_start(buffer)
        if rescue >= 0 and (primary < 0 or rescue < primary):
            return self.rescue
        return self.primary

    def is_possibly_command(self, buffer: str) -> bool:
        return self.primary.is_possibly_command(buffer) or self.rescue.is_possibly_command(buffer)

    def find_start(self, buffer: str) -> int:
        starts = [pos for pos in (self.primary.find_start(buffer), self.rescue.find_start(buffer)) if pos >= 0]
        return min(starts) if starts else -1

    def partial_tail(self, buffer: str) -> int:
        tails = [pos for pos in (self.primary.partial_tail(buffer), self.rescue.partial_tail(buffer)) if pos > 0]
        return min(tails) if tails else -1

    def complete(self, buffer: str, streaming: bool = False) -> bool:
        return self._reader(buffer).complete(buffer, streaming=streaming)

    def extract(self, buffer: str) -> tuple[tuple[str, list[str], str] | None, str | None]:
        reader = self._reader(buffer)
        call, rest = reader.extract(buffer)
        if reader is self.rescue and call is not None:
            self.rescued += 1
            note(f"Rescued a {call[0]} call the model wrote as an <invoke> block under the {self.name} format "
                 f"— it runs as usual and is stored in {self.name} syntax (rescue #{self.rescued} of this reply)")
        return call, rest

    def call_end(self, buffer: str) -> int:
        return self._reader(buffer).call_end(buffer)

    def unterminated(self, buffer: str) -> str | None:
        return self.primary.unterminated(buffer) or self.rescue.unterminated(buffer)

    def _spans(self, text: str) -> Iterator[tuple[int, int, CommandFormat]]:
        """Every complete call block of the text in order, as `(start, end, the format that
        reads it)`; a call opened and never closed is stepped over."""
        at = 0
        while (start := self.find_start(text[at:])) >= 0:
            start += at
            reader = self._reader(text[start:])
            end = reader.call_end(text[start:])
            if end <= 0:
                at = start + 1
                continue
            yield start, start + end, reader
            at = start + end

    def extract_all(self, text: str) -> list[tuple[str, list[str]]]:
        return [call for start, end, reader in self._spans(text) for call in reader.extract_all(text[start:end])]

    def _cut_block(self, block: str, keep: int) -> str:
        return self._reader(block)._cut_block(block, keep)

    def rewritten(self, text: str) -> str:
        """The text with every block the rescue syntax read written in the active syntax, one
        call per line; a block of nothing (a closer nothing opened) is dropped."""
        out, at, blocks, calls = [], 0, 0, 0
        for start, end, reader in self._spans(text):
            if reader is not self.rescue:
                continue
            invokes = [invoke for _, _, block in self.rescue.blocks(text[start:end]) for invoke in block]
            out.append(text[at:start])
            out.append("\n".join(self.primary.render_tool_call(name, dict(arguments)) for name, arguments in invokes))
            at, blocks, calls = end, blocks + 1, calls + len(invokes)
        if not blocks:
            return text
        out.append(text[at:])
        info(f"Stored turn rewritten: {blocks} <invoke> block(s) holding {calls} call(s) written in {self.name} syntax")
        return "".join(out)
