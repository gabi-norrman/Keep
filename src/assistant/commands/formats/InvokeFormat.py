"""The `<function_calls>` / `<invoke>` call syntax Claude writes its tool calls in, read and rendered for Keep's commands.

A call is a block between `<function_calls>` and `</function_calls>` holding one
`<invoke name="…">` per call, each with `<parameter name="…">value</parameter>` elements: the
spelling Anthropic's models were trained to call tools in (the API turns it into `tool_use`
blocks only when a request declares tools; with none declared, the text comes through as
written — probed 2026-10-20 on Opus 5.5 and Haiku 5.5, streaming and not, see the plan). A
value is raw text, a string without quotes and a number bare; nothing is escaped, and one
leading and one trailing newline are stripped so a value may stand on its own line. The
parameters are named: `NamedParameterFormat` orders them through the signature table, and a
variadic last parameter takes a JSON list. A declaration keeps Keep's own signature text —
`` `dim(light_name: str, dim_percentage: int)` `` names the parameters the invoke writes and is
a fraction of a JSON schema's length — and an example renders as the block itself.
`render_call` is also what a backend that parses the model's calls itself (`OllamaLLM`,
`Ds4LLM`) writes a parsed call back into when this is the process's format.

The format is offered by name (`command_format: invoke` on a tier) and is no backend's default:
Claude follows Keep's syntax, so whether the trained spelling conforms better is a measurement
to make, not a given.
"""
import json
import re
from typing import Sequence

from common.logging import trace

from .KeepFormat import render_declaration as keep_declaration
from .NamedParameterFormat import NamedParameterFormat, argument_text
from .Signatures import Declaration, Value

OPEN = "<function_calls>"
CLOSE = "</function_calls>"
INVOKE_OPEN = "<invoke"
INVOKE_CLOSE = "</invoke>"
PARAMETER_OPEN = "<parameter"
PARAMETER_CLOSE = "</parameter>"
_INVOKE_RE = re.compile(rf'{INVOKE_OPEN}\s+name="([^"]*)"\s*>(.*?){INVOKE_CLOSE}', re.S)
_PARAMETER_RE = re.compile(rf'{PARAMETER_OPEN}\s+name="([^"]*)"\s*>\n?(.*?)\n?{PARAMETER_CLOSE}', re.S)
_INVOKE_NAME_RE = re.compile(rf'{INVOKE_OPEN}\s+name="([^"]*)"')

# The shape Anthropic's own tool-use instruction teaches, with Keep's declarations in place of
# the schema listing: the functions stand throughout the prompt and the toolkits as
# `name(parameter: type, ...)`, and the parameter names are the ones an invoke writes. No
# backticked call of a real command stands here: the rewriter would render it.
INSTRUCTIONS = f"""\
In this environment you have access to a set of functions you can use to act for the user. You invoke a function by \
writing a block like the following as part of your reply:

{OPEN}
{INVOKE_OPEN} name="$FUNCTION_NAME">
{PARAMETER_OPEN} name="$PARAMETER_NAME">$PARAMETER_VALUE{PARAMETER_CLOSE}
...
{INVOKE_CLOSE}
{CLOSE}

A string or number value is written as is, without quotes. A parameter declared with a trailing `...` takes any \
number of values, written as one JSON array (`["email", "web"]`). A block may hold several invokes; they run in order.

Your functions are declared throughout this prompt and in every toolkit you load, each as `name(parameter: type, ...)`: \
the parameter names are the ones you write in `<parameter name="…">`, a parameter with `= default` is optional, \
a type like `YYYY-MM-DD` says how the value is spelled. There is one set of functions: a function declared further \
down in this prompt and one declared in a toolkit you loaded are equally available to you and are invoked in exactly \
the same format. Never say that a capability is missing because a function was not listed here, and never load a \
toolkit to look for a function that is already declared in front of you.

A function runs the moment you write its {OPEN} block, so never write one you do not mean to run, and never put \
the block in a code fence or quote it — a fenced block is treated as text and the action silently never runs. To \
merely mention a function, write its name as a plain word; a name on its own never runs."""


def render_call(name: str, arguments: Sequence[tuple[str, str]]) -> str:
    """One `<function_calls>` block invoking `name` with the `(parameter, value text)` pairs in order."""
    lines = [OPEN, f'{INVOKE_OPEN} name="{name}">']
    lines += [f'{PARAMETER_OPEN} name="{key}">{text}{PARAMETER_CLOSE}' for key, text in arguments]
    lines += [INVOKE_CLOSE, CLOSE]
    return "\n".join(lines)


class InvokeFormat(NamedParameterFormat):
    """Anthropic's `<invoke>` syntax: a `<function_calls>` block of named-parameter invokes,
    mapped to Keep's positional parameters through the signature table."""

    name = "invoke"
    OPEN = OPEN
    CLOSE = CLOSE

    # --- rendering -------------------------------------------------------------------------

    def instructions(self) -> str:
        return INSTRUCTIONS

    def render_declaration(self, declaration: Declaration) -> str:
        """Keep's signature text, as written: it names the parameters an invoke writes."""
        return keep_declaration(declaration)

    @classmethod
    def render_tool_call(cls, name: str, arguments) -> str:
        """A parsed call as the block the model would have written: each argument spelled by
        `argument_text` (a string as is, a number bare, a list as JSON)."""
        return render_call(name, [(key, argument_text(value)) for key, value in cls.arguments_dict(arguments).items()])

    def render_example(self, name: str, values: Sequence[Value], declaration: Declaration | None) -> str:
        names = declaration.names if declaration else []
        arguments = []
        for i, value in enumerate(values):
            if declaration and declaration.variadic and i >= len(names) - 1:
                rest = [v.value for v in values[i:]]
                arguments.append((names[-1], json.dumps(rest, ensure_ascii=False)))
                break
            arguments.append((names[i] if i < len(names) else f"arg{i + 1}", value.value))
        return render_call(name, arguments)

    # --- parsing ---------------------------------------------------------------------------

    def extract(self, buffer: str) -> tuple[tuple[str, list[str], str] | None, str | None]:
        start = buffer.find(OPEN)
        end = buffer.find(CLOSE, start) if start >= 0 else -1
        if end < 0:
            return None, None
        invokes = self._invokes(buffer[start + len(OPEN):end])
        if not invokes:
            trace(f"invoke format: a function_calls block without an invoke, dropped: {buffer[start:end]!r}")
            return None, buffer[end + len(CLOSE):]
        name, arguments = invokes[0]
        params = self._positional(name, arguments)
        # The further invokes of the block go back as blocks of their own, so the processor
        # reads them one by one from the rest.
        rest = "\n".join(render_call(n, a) for n, a in invokes[1:])
        return (name, params, ", ".join(params)), rest + buffer[end + len(CLOSE):]

    def unterminated(self, buffer: str) -> str | None:
        stripped = buffer.strip()
        if not stripped.startswith(OPEN):
            return None
        match = _INVOKE_NAME_RE.search(stripped)
        return match.group(1).lower() if match else None

    def extract_all(self, text: str) -> list[tuple[str, list[str]]]:
        found = []
        at = 0
        while (start := text.find(OPEN, at)) >= 0 and (end := text.find(CLOSE, start)) >= 0:
            for name, arguments in self._invokes(text[start + len(OPEN):end]):
                found.append((name, self._positional(name, arguments)))
            at = end + len(CLOSE)
        return found

    @staticmethod
    def _invokes(block: str) -> list[tuple[str, list[tuple[str, str]]]]:
        """The `(name, [(parameter, value)])` of every invoke in a block, in order."""
        return [(m.group(1).lower(), [(p.group(1), p.group(2)) for p in _PARAMETER_RE.finditer(m.group(2))])
                for m in _INVOKE_RE.finditer(block)]
