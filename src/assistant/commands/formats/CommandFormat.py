"""The call syntax the LLM writes commands in: how a call is found in the reply stream and read out of it.

Keep has one command system, reached by the LLM writing calls as text. Which text — Keep's own
`turn_on(Kitchen lamp)`, Qwen's `<tool_call>` block, DeepSeek's DSML block, Claude's `<invoke>`
block — is the format. One implementation of this interface parses one syntax, and every one
hands `CommandProcessor` the same `(name, params, display)`, so the commands, their descriptions
and the approval gate never see which syntax the model wrote. `CommandFormats.active()` names the one in use.
"""
import json
from abc import ABC, abstractmethod
from typing import Sequence

from .Signatures import Declaration, Value


class CommandFormat(ABC):
    """One call syntax: finds, delimits and reads the calls in the text the LLM streams, and
    renders the texts that teach it (the COMMAND SYSTEM instructions, a declaration, an
    example) in the same syntax."""

    name: str  # "keep" | "qwen" | "dsml" | "invoke"

    def __init__(self, possible_commands: list[str]):
        # The command names a call may carry. Kept as the list given, not a copy, so a name a
        # caller appends afterwards is recognised too.
        self.possible_commands = possible_commands

    @abstractmethod
    def instructions(self) -> str:
        """The {COMMAND_SYSTEM} text of the prompt: how a call is written in this syntax, what
        is not one, and how a command is mentioned without calling it."""

    @abstractmethod
    def render_declaration(self, declaration: Declaration) -> str:
        """A command's declaration — `declaration.command` with `declaration.params` (name, type,
        default) and the variadic flag — as the texts show it in this syntax, delimiters
        included: it replaces the whole backticked `` `name(param: type, ...)` `` of the source."""

    @abstractmethod
    def render_example(self, name: str, values: Sequence[Value], declaration: Declaration | None) -> str:
        """An example call of `name` with `values` in order, as the texts show it in this
        syntax, delimiters included. `declaration` is the command's declaration from the
        signature table (None when the table has none), so a named-parameter format pairs each
        value with the declared name in its position — fewer values than names is an example
        leaving optional parameters out, and the values past the last name of a variadic
        declaration all belong to that name."""

    @classmethod
    @abstractmethod
    def render_tool_call(cls, name: str, arguments) -> str:
        """A call a server parsed out of the model's reply — `name` and its `arguments`, a dict
        or the JSON object text — written back as call text in this syntax. A backend whose
        server takes the model's block (Ollama, ds4) yields this in its place, rendered in the
        *active* format, so the processor reads the call whatever syntax the model wrote."""

    @staticmethod
    def arguments_dict(arguments) -> dict:
        """A parsed call's arguments as a dict: the JSON object text a server hands over decoded,
        a dict as is, anything else (text that is not JSON, a bare value) as the one `value`."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                return {"value": arguments}
        return arguments if isinstance(arguments, dict) else {"value": arguments}

    @abstractmethod
    def is_possibly_command(self, buffer: str) -> bool:
        """True while the buffer is, or may still grow into, a call — the streaming processor
        then holds it back instead of releasing it as reply text. An empty buffer counts."""

    @abstractmethod
    def find_start(self, buffer: str) -> int:
        """The position where the earliest call starts in the buffer, or -1 when none can
        start in it."""

    @abstractmethod
    def partial_tail(self, buffer: str) -> int:
        """The position where a possible call opener begins at the end of the buffer (a
        suffix that is a prefix of one), or -1 when the buffer ends on no such thing."""

    @abstractmethod
    def complete(self, buffer: str, streaming: bool = False) -> bool:
        """True when a whole call — opener to closer — is in the buffer. `streaming` says the
        buffer may still grow, so an ambiguous closer is not trusted yet."""

    @abstractmethod
    def extract(self, buffer: str) -> tuple[tuple[str, list[str], str] | None, str | None]:
        """The first call in the buffer read out: `((name, params, display), rest)` — the
        command name lowercased, its parameters as positional strings, the display text of
        those parameters for logs and echoes, and the text after the call — or `(None, None)`
        when the buffer holds no complete call."""

    @abstractmethod
    def unterminated(self, buffer: str) -> str | None:
        """The name of the command the buffer opens but never closes, for the report that
        goes back to the LLM when its stream ended inside a call, or None when the buffer
        opens no call (a bare name is a mention, not a call)."""

    @abstractmethod
    def extract_all(self, text: str) -> list[tuple[str, list[str]]]:
        """Every call in a complete text, in order, as `(name, params)` — the cache layer's
        view of a stored reply, so no display text."""
