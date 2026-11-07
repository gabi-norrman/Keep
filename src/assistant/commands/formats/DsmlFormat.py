"""The DSML call syntax DeepSeek V4.1 writes, read and rendered for Keep's commands in the V4.1 spelling ds4-server parses.

A call is a block between `<｜DSML｜ calls>` and `</｜DSML｜ calls>` holding one
`<｜DSML｜ invoke name="…">` per call, each with `<｜DSML｜ parameter name="…" string="true|false">`
values: raw text when `string="true"`, JSON when `string="false"` (decoded here to the string a
positional parser hands `CommandProcessor`). The spelling with the spaces is DeepSeek V4.1's —
`DS41_TOOL_CALLS_START` in ds4_server.c; V4 wrote `<｜DSML｜tool_calls>` — and ds4-server's
own parser produces the V4.1 spelling in its stream, so the format emits and parses that one
alone. The parameters are named: `NamedParameterFormat` orders them through the signature
table. A declaration renders as the line DeepSeek's own encoder (`encoding/encoding.py` of the
model repository, checked 2026-10-21 for V4.1-Flash) lists a tool under `### Available Tool
Schemas`: the bare function object, `{"name": …, "parameters": {…}}`, no OpenAI wrapper, no
backticks, spaced `json.dumps` — ds4-server writes the same line, compact — and an example as
the DSML block itself; `render_call` is also what `Ds4LLM` writes a parsed `tool_calls` entry
back into, so the stream reaches the processor as this text. The instructions are DeepSeek's
own `## Tools` text with Keep's paragraph in place of the schema listing: on ds4 the model also
reads the server's block with the one tool Keep declares to have the calls parsed at all, so
the paragraph says the tools in front of the prompt and the ones throughout it are one set.

Under every other format this one is a rescue (`RescueFormat`): a block the model writes in
this shape anyway is read by it, run, and stored in the active syntax. Three slips are read
as the call the model meant, all from one Telegram dialog on the ds4 host on 2026-11-07, V4.1
Flash under Keep's syntax, five replies in a row and not one call run: the argument written as
a second attribute of the invoke tag, `invoke name="load_toolkit" name="developer"` (every
attribute after the first is a string parameter of that name; a stray parameter closer
without an opener in the body is ignored), the invoke self-closed with `/>`, and the block
left without its `calls` closer — read once the stream has ended, up to the next opener or
the end of the reply, when it holds a complete invoke. ds4-server itself read the one
well-formed block of the five to a `load_toolkit` call with no arguments (its parser takes the
first `name` attribute and no parameter element), which Keep cannot repair.
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
# An invoke: the name, any further attributes (the slip that writes the argument there), then
# either `/>` or a body up to the closer.
_INVOKE_RE = re.compile(rf'{re.escape(INVOKE_OPEN)}\s+name="([^"]*)"((?:\s+[\w-]+="[^"]*")*)\s*'
                        rf'(?:/>|>(.*?){re.escape(INVOKE_CLOSE)})', re.S)
_ATTRIBUTE_RE = re.compile(r'([\w-]+)="([^"]*)"')
_PARAMETER_RE = re.compile(rf'{re.escape(PARAMETER_OPEN)}\s+name="([^"]*)"(?:\s+string="(true|false)")?\s*>'
                           rf'(.*?){re.escape(PARAMETER_CLOSE)}', re.S)
_INVOKE_NAME_RE = re.compile(rf'{re.escape(INVOKE_OPEN)}\s+name="([^"]*)"')

# DeepSeek's own `## Tools` text for V4.1 (encoding.py of the model repository), with Keep's
# paragraph where the schema listing stands there: the tools are declared throughout the prompt
# and the toolkits, and the one ds4-server lists in its own block in front of the prompt is one
# of the same set. No backticked call of a real command stands here: the rewriter would render it.
INSTRUCTIONS = f"""\
## Tools

You have access to a set of tools to help answer the user's question. You can invoke tools by writing a "{OPEN}" block \
like the following:

{OPEN}
{INVOKE_OPEN} name="$TOOL_NAME">
{PARAMETER_OPEN} name="$PARAMETER_NAME" string="true|false">$PARAMETER_VALUE{PARAMETER_CLOSE}
...
{INVOKE_CLOSE}
{INVOKE_OPEN} name="$TOOL_NAME2">
...
{INVOKE_CLOSE}
{CLOSE}

String parameters should be specified as is and set `string="true"`. For all other types (numbers, booleans, arrays, \
objects), pass the value in JSON format and set `string="false"`.

If thinking_mode is enabled (triggered by <think>), you MUST output your complete reasoning inside <think>...</think> \
BEFORE any tool calls or final response.

Otherwise, output directly after </think> with tool calls or final response.

### Available Tool Schemas

Your tools are declared throughout this prompt and in every toolkit you load, each as a JSON schema line of the shape \
{{"name": ..., "parameters": ...}}. There is one set of tools: a tool listed in a Tools block in front of this prompt, \
one declared further down in it and one declared in a toolkit you loaded are equally available to you and are all \
invoked in exactly the same format. Never say that a capability is missing because a tool was not listed in front of \
this prompt, and never load a toolkit to look for a tool that is already declared in front of you.

You MUST strictly follow the above defined tool name and parameter schemas to invoke tool calls.

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
    INVOKE_RE = _INVOKE_RE

    # --- rendering -------------------------------------------------------------------------

    def instructions(self) -> str:
        return INSTRUCTIONS

    def render_declaration(self, declaration: Declaration) -> str:
        """The schema line of DeepSeek's `### Available Tool Schemas`: the bare function object,
        spaced as its encoder's `json.dumps` writes it, without the OpenAI wrapper or backticks."""
        return json.dumps(self.function_schema(declaration)["function"], ensure_ascii=False)

    @classmethod
    def render_tool_call(cls, name: str, arguments) -> str:
        return render_tool_call(name, arguments)

    def _render_block(self, name: str, arguments: Sequence[tuple[str, str, bool]]) -> str:
        return render_call(name, arguments)

    # --- parsing ---------------------------------------------------------------------------

    def _span(self, buffer: str, at: int = 0, closed_only: bool = False) -> tuple[int, int, int, int] | None:
        """`(start, end, body start, body end)` of the first block from `at`: `end` is where
        the text after the block begins. A block the model never closed ends at the next
        opener or the end of the text, and counts only when its body holds a complete invoke
        — unless `closed_only` (the stream may still grow). None when no block is complete."""
        start = buffer.find(OPEN, at)
        if start < 0:
            return None
        body = start + len(OPEN)
        close = buffer.find(CLOSE, body)
        if close >= 0:
            return start, close + len(CLOSE), body, close
        if closed_only:
            return None
        end = buffer.find(OPEN, body)
        end = len(buffer) if end < 0 else end
        if not _INVOKE_RE.search(buffer, body, end):
            return None
        trace(f"dsml format: a calls block without its closer, read up to {'the next opener' if end < len(buffer) else 'the end'}")
        return start, end, body, end

    def complete(self, buffer: str, streaming: bool = False) -> bool:
        return self._span(buffer, closed_only=streaming) is not None

    def call_end(self, buffer: str) -> int:
        span = self._span(buffer)
        return span[1] if span else -1

    def extract(self, buffer: str) -> tuple[tuple[str, list[str], str] | None, str | None]:
        span = self._span(buffer)
        if span is None:
            return None, None
        start, end, body, body_end = span
        invokes = self._invokes(buffer[body:body_end])
        if not invokes:
            trace(f"dsml format: a calls block without an invoke, dropped: {buffer[start:end]!r}")
            return None, buffer[end:]
        name, arguments = invokes[0]
        params = self._positional(name, self._values(name, arguments))
        # The further invokes of the block go back as blocks of their own, so the processor
        # reads them one by one from the rest.
        rest = "\n".join(render_call(n, self._renderable(n, a)) for n, a in invokes[1:])
        return (name, params, ", ".join(params)), rest + buffer[end:]

    def unterminated(self, buffer: str) -> str | None:
        stripped = buffer.strip()
        if not stripped.startswith(OPEN):
            return None
        match = _INVOKE_NAME_RE.search(stripped)
        return match.group(1).lower() if match else None

    def extract_all(self, text: str) -> list[tuple[str, list[str]]]:
        return [(name, self._positional(name, self._values(name, arguments)))
                for _, _, invokes in self._blocks(text) for name, arguments in invokes]

    def blocks(self, text: str) -> list[tuple[int, int, list[tuple[str, list[tuple[str, str]]]]]]:
        """Every block of the text in order as `(start, end, invokes)` — the invokes as
        `(name, [(parameter, value)])` with the parameter names as the model wrote them and
        each value as the positional text, so a caller may render the call in another syntax."""
        return [(start, end, [(name, self._values(name, arguments)) for name, arguments in invokes])
                for start, end, invokes in self._blocks(text)]

    def _blocks(self, text: str) -> list[tuple[int, int, list[tuple[str, list[tuple[str, str, bool]]]]]]:
        found = []
        at = 0
        while span := self._span(text, at):
            start, end, body, body_end = span
            found.append((start, end, self._invokes(text[body:body_end])))
            at = end
        return found

    @staticmethod
    def _invokes(block: str) -> list[tuple[str, list[tuple[str, str, bool]]]]:
        """The `(name, [(parameter, value as written, is string)])` of every invoke in a
        block, in order; `string="false"` is the one spelling that makes a value JSON. An
        attribute after the name is a string parameter the model wrote in the tag."""
        found = []
        for m in _INVOKE_RE.finditer(block):
            arguments = [(key, value, True) for key, value in _ATTRIBUTE_RE.findall(m.group(2))]
            if arguments:
                trace(f"dsml format: {m.group(1)} has {len(arguments)} parameter(s) written as attributes of the invoke tag")
            arguments += [(p.group(1), p.group(3), p.group(2) != "false") for p in _PARAMETER_RE.finditer(m.group(3) or "")]
            found.append((m.group(1).lower(), arguments))
        return found

    @staticmethod
    def _values(name: str, arguments: list[tuple[str, str, bool]]) -> list[tuple[str, str]]:
        """The `(parameter, positional string)` pairs of an invoke's arguments."""
        return [(key, _value_text(raw, is_string, name)) for key, raw, is_string in arguments]

    @staticmethod
    def _renderable(name: str, arguments: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
        """An invoke's arguments as `render_call` takes them: a string value unescaped (the
        renderer escapes it again), a JSON value as written."""
        return [(key, _value_text(raw, True, name) if is_string else raw, is_string) for key, raw, is_string in arguments]
