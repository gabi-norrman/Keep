"""The DSML call syntax DeepSeek V4.1 writes, read and rendered for Keep's commands in the V4.1 spelling ds4-server parses.

A call is a block between `<｜DSML｜ calls>` and `</｜DSML｜ calls>` holding one
`<｜DSML｜ invoke name="…">` per call, each with `<｜DSML｜ parameter name="…" string="true|false">`
values: raw text when `string="true"`, JSON when `string="false"` (decoded here to the string a
positional parser hands `CommandProcessor`). The spelling with the spaces is DeepSeek V4.1's —
`DS41_TOOL_CALLS_START` in ds4_server.c; V4 wrote `<｜DSML｜tool_calls>` — and ds4-server's
own parser produces the V4.1 spelling in its stream, so the format emits and parses that one
alone. The parameters are named: `NamedParameterFormat` orders them through the signature
table. A declaration renders as the JSON function schema DeepSeek's `## Tools` block lists,
an example as the DSML block itself; `render_call` is also what `Ds4LLM` writes a parsed
`tool_calls` entry back into, so the stream reaches the processor as this text.
"""
import json
import re
from typing import Sequence

from common.logging import trace

from .NamedParameterFormat import NamedParameterFormat, argument_text
from .Signatures import Declaration, Value

DSML = "｜DSML｜"
OPEN = f"<{DSML} calls>"
CLOSE = f"</{DSML} calls>"
INVOKE_OPEN = f"<{DSML} invoke"
INVOKE_CLOSE = f"</{DSML} invoke>"
PARAMETER_OPEN = f"<{DSML} parameter"
PARAMETER_CLOSE = f"</{DSML} parameter>"
# ds4-server's rule for a closing tag inside a string value: written as `&lt;/｜DSML｜ parameter>`,
# and that spelling itself as `&amp;lt;/｜DSML｜ parameter>`; other entities are left alone.
_ESCAPED_CLOSE = "&lt;" + PARAMETER_CLOSE[1:]
_ESCAPED_ESCAPE = "&amp;lt;" + PARAMETER_CLOSE[1:]
_INVOKE_RE = re.compile(rf'{re.escape(INVOKE_OPEN)}\s+name="([^"]*)"\s*>(.*?){re.escape(INVOKE_CLOSE)}', re.S)
_PARAMETER_RE = re.compile(rf'{re.escape(PARAMETER_OPEN)}\s+name="([^"]*)"(?:\s+string="(true|false)")?\s*>'
                           rf'(.*?){re.escape(PARAMETER_CLOSE)}', re.S)
_INVOKE_NAME_RE = re.compile(rf'{re.escape(INVOKE_OPEN)}\s+name="([^"]*)"')

# ds4-server's own `## Tools` block for a V4.1 model (append_tools_prompt_text in ds4_server.c,
# which is what the model reads when a request declares tools), without the schema listing —
# Keep's functions are declared throughout the prompt and the toolkits, and the model must
# take them all as its tools. No backticked call of a real command stands here: the rewriter
# would render it.
INSTRUCTIONS = f"""\
## Tools

You can invoke tools using this format:

{OPEN}
{INVOKE_OPEN} name="$TOOL_NAME">
{PARAMETER_OPEN} name="$PARAMETER_NAME" string="true|false">$PARAMETER_VALUE{PARAMETER_CLOSE}
{INVOKE_CLOSE}
{CLOSE}

String values use string="true"; all other values use JSON and string="false". Inside string values only, escape a \
literal {PARAMETER_CLOSE} as {_ESCAPED_CLOSE}. To write that escaped spelling literally, use {_ESCAPED_ESCAPE}. Other \
HTML entities are unchanged.

Finish reasoning with </think> before tool calls or a final response.

### Available Tool Schemas

Your tools are declared throughout this prompt and in every toolkit you load, each as a JSON function schema line. \
There is one set of tools: a function declared further down in this prompt and one declared in a toolkit you loaded \
are equally available to you and are all invoked in exactly the same format. Never say that a capability is missing \
because a function was not listed here, and never load a toolkit to look for a function that is already declared in \
front of you.

You MUST strictly follow the above defined tool name and parameter schemas to invoke tool calls. Use the exact \
parameter names from the schemas.

A tool runs the moment you write its {OPEN} block, so never write one you do not mean to run. To merely mention a \
tool, write its name as a plain word; a name on its own never runs."""


def render_call(name: str, arguments: Sequence[tuple[str, str, bool]]) -> str:
    """One DSML block calling `name` with the `(parameter, value text, is string)` triples in
    order: a string value is written raw with `string="true"`, any other as the JSON text given
    with `string="false"`."""
    lines = [OPEN, f'{INVOKE_OPEN} name="{name}">']
    for key, text, is_string in arguments:
        if is_string:
            text = text.replace(_ESCAPED_CLOSE, _ESCAPED_ESCAPE).replace(PARAMETER_CLOSE, _ESCAPED_CLOSE)
        lines.append(f'{PARAMETER_OPEN} name="{key}" string="{"true" if is_string else "false"}">{text}{PARAMETER_CLOSE}')
    lines += [INVOKE_CLOSE, CLOSE]
    return "\n".join(lines)


def render_tool_call(name: str, arguments) -> str:
    """A parsed `tool_calls` entry — `arguments` the JSON object text or the decoded dict —
    as the DSML text the model wrote: a string value raw, anything else as JSON."""
    arguments = NamedParameterFormat.arguments_dict(arguments)
    return render_call(name, [(key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                               isinstance(value, str)) for key, value in arguments.items()])


def _value_text(raw: str, is_string: bool, name: str) -> str:
    """A parameter's value as a positional string: a raw value with the closing-tag escapes
    resolved, a JSON value decoded (a JSON string to itself, anything else spelled the way the
    model writes it); JSON that does not parse is kept as written."""
    if is_string:
        return raw.replace(_ESCAPED_ESCAPE, "\0").replace(_ESCAPED_CLOSE, PARAMETER_CLOSE).replace("\0", _ESCAPED_CLOSE)
    try:
        return argument_text(json.loads(raw))
    except ValueError:
        trace(f"dsml format: {name} has a string=\"false\" value that is not JSON, kept as written: {raw!r}")
        return raw


class DsmlFormat(NamedParameterFormat):
    """DeepSeek's DSML syntax in the V4.1 spelling: a block of named-parameter invokes, mapped
    to Keep's positional parameters through the signature table."""

    name = "dsml"
    OPEN = OPEN
    CLOSE = CLOSE

    # --- rendering -------------------------------------------------------------------------

    def instructions(self) -> str:
        return INSTRUCTIONS

    @classmethod
    def render_tool_call(cls, name: str, arguments) -> str:
        return render_tool_call(name, arguments)

    def render_example(self, name: str, values: Sequence[Value], declaration: Declaration | None) -> str:
        names = declaration.names if declaration else []
        arguments = []
        for i, value in enumerate(values):
            if declaration and declaration.variadic and i >= len(names) - 1:
                rest = [v.value for v in values[i:]]
                arguments.append((names[-1], json.dumps(rest, ensure_ascii=False), False))
                break
            key = names[i] if i < len(names) else f"arg{i + 1}"
            if value.kind == "bare":
                arguments.append((key, value.text, False))
            else:
                arguments.append((key, value.value, True))
        return render_call(name, arguments)

    # --- parsing ---------------------------------------------------------------------------

    def extract(self, buffer: str) -> tuple[tuple[str, list[str], str] | None, str | None]:
        start = buffer.find(OPEN)
        end = buffer.find(CLOSE, start) if start >= 0 else -1
        if end < 0:
            return None, None
        invokes = self._invokes(buffer[start + len(OPEN):end])
        if not invokes:
            trace(f"dsml format: a calls block without an invoke: {buffer[start:end]!r}")
            return None, None
        name, arguments = invokes[0]
        params = self._positional(name, self._values(name, arguments))
        # The further invokes of the block go back as blocks of their own, so the processor
        # reads them one by one from the rest.
        rest = "\n".join(render_call(n, self._renderable(n, a)) for n, a in invokes[1:])
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
                found.append((name, self._positional(name, self._values(name, arguments))))
            at = end + len(CLOSE)
        return found

    @staticmethod
    def _invokes(block: str) -> list[tuple[str, list[tuple[str, str, bool]]]]:
        """The `(name, [(parameter, value as written, is string)])` of every invoke in a
        block, in order; `string="false"` is the one spelling that makes a value JSON."""
        return [(m.group(1).lower(), [(p.group(1), p.group(3), p.group(2) != "false")
                                      for p in _PARAMETER_RE.finditer(m.group(2))])
                for m in _INVOKE_RE.finditer(block)]

    @staticmethod
    def _values(name: str, arguments: list[tuple[str, str, bool]]) -> list[tuple[str, str]]:
        """The `(parameter, positional string)` pairs of an invoke's arguments."""
        return [(key, _value_text(raw, is_string, name)) for key, raw, is_string in arguments]

    @staticmethod
    def _renderable(name: str, arguments: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
        """An invoke's arguments as `render_call` takes them: a string value unescaped (the
        renderer escapes it again), a JSON value as written."""
        return [(key, _value_text(raw, True, name) if is_string else raw, is_string) for key, raw, is_string in arguments]
