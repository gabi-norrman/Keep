"""The `<tool_call>` call syntax Qwen 3.x writes and Ollama's Qwen chat templates teach, read and rendered for Keep's commands.

A call is a block between `<tool_call>` and `</tool_call>` holding one `<function=name>` per
invoke, each with `<parameter=name>` values (one leading and one trailing newline stripped).
The parameters are named, Keep's commands take positional strings: the signature table
(`Signatures.table`, read from the declarations in the texts) maps each name to its position;
a command or a parameter the table does not know keeps the order the model wrote. The last
parameter of a variadic declaration (`load_toolkit(name: str, ...)`) is one array-typed
parameter whose value — a JSON list, or names separated by commas — is split back into the
positional values. The texts are rendered the way the model was trained to read tools: a
declaration becomes the JSON function schema line Ollama's `<tools>` block shows, an example
the `<tool_call>` block itself. `render_call` is also what `OllamaLLM` writes a parsed
`message.tool_calls` entry back into, so the stream reaches the processor as this text.
"""
import json
import re
from typing import Sequence

from common.logging import trace

from .NamedParameterFormat import NamedParameterFormat, argument_text  # noqa: F401  (argument_text: OllamaLLM's spelling of a value)
from .Signatures import Declaration, Value

OPEN = "<tool_call>"
CLOSE = "</tool_call>"
_FUNCTION_RE = re.compile(r"<function=([^>\s]+)\s*>\n?(.*?)\n?</function>", re.S)
_PARAMETER_RE = re.compile(r"<parameter=([^>\s]+)\s*>\n?(.*?)\n?</parameter>", re.S)

# Ollama's Qwen template puts its own `# Tools` block, with the one tool the request declares,
# in front of the system prompt; this is the same instruction without the `<tools>` listing,
# since Keep's functions are declared throughout the prompt and the toolkits — the one point
# the model must take from here is that those declarations are its tools, every one of them,
# whether or not a `<tools>` block repeats them. The format example and the reminder are the
# template's own text, so the model reads what it was trained on. No backticked call of a
# real command stands here: the rewriter would render it.
INSTRUCTIONS = """\
# Tools

Your functions are declared throughout this prompt and in every toolkit you load, each as a JSON function schema \
line. There is one set of functions: a function declared in a `<tools>` block, one declared further down in this \
prompt and one declared in a toolkit you loaded are all equally available to you and are all called in exactly the \
same format. Never say that a capability is missing because a function was not listed in a `<tools>` block, and \
never load a toolkit to look for a function that is already declared in front of you.

If you choose to call a function ONLY reply in the following format with NO suffix:

<tool_call>
<function=example_function_name>
<parameter=example_parameter_1>
value_1
</parameter>
<parameter=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter>
</function>
</tool_call>

<IMPORTANT>
Reminder:
- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags
- Required parameters MUST be specified
- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after
- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls
</IMPORTANT>

A function runs the moment you write its <tool_call> block, so never write one you do not mean to run. To merely \
mention a function, write its name as a plain word; a name on its own never runs."""


def render_call(name: str, arguments: Sequence[tuple[str, str]]) -> str:
    """One `<tool_call>` block calling `name` with the `(parameter, value text)` pairs in order."""
    lines = [OPEN, f"<function={name}>"]
    for key, text in arguments:
        lines += [f"<parameter={key}>", text, "</parameter>"]
    lines += ["</function>", CLOSE]
    return "\n".join(lines)


class QwenFormat(NamedParameterFormat):
    """Qwen's `<tool_call>` syntax: a block of named-parameter invokes, mapped to Keep's
    positional parameters through the signature table."""

    name = "qwen"
    OPEN = OPEN
    CLOSE = CLOSE

    # --- rendering -------------------------------------------------------------------------

    def instructions(self) -> str:
        return INSTRUCTIONS

    @classmethod
    def render_tool_call(cls, name: str, arguments) -> str:
        """A parsed call as the `<tool_call>` text the model wrote: each argument spelled by
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
            trace(f"Qwen format: a <tool_call> block without a <function=…> invoke: {buffer[start:end]!r}")
            return None, None
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
        match = re.search(r"<function=([^>\s]+)", stripped)
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
        """The `(name, [(parameter, value)])` of every `<function=…>` in a block, in order."""
        return [(m.group(1).lower(), [(p.group(1), p.group(2)) for p in _PARAMETER_RE.finditer(m.group(2))])
                for m in _FUNCTION_RE.finditer(block)]
