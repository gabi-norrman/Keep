"""The parameter names the texts declare for every command: the table a named-parameter format maps its calls to positions with.

The commands take positional strings; the Qwen and DSML formats name their parameters. The
bridge is the texts themselves: `res/prompt.md` and every toolkit declare each command once as
`` `name(param: type, ...)` `` — this module reads those declarations into `name → [param names]`
and reads the examples (`` `name("value", 1)` ``) with the real `CommandParser`, so a rewriter
pairs an example's values with the declared names the way the runtime would read them.

Two shapes of backticked call occur in the texts and nothing else is touched:

- a declaration — `` `dim(light_name: str, dim_percentage: int)` ``: comma-separated `name: type`
  items, each with an optional `= default`, and a final `...` for a variadic (`` `load_toolkit(name:
  str, ...)` `` — one parameter taking any number of values). The type is whatever stands after
  the colon (`str`, `int`, `YYYY-MM-DD`, `HH:MM`); a renderer that needs a schema type maps it.
- an example — every other call: a plain function call in the canonical form, strings in double
  quotes (`"…"`, or a triple-quoted block for a long value), numbers and booleans bare, `\"` for a
  quote inside a string. `` `restart_server()` `` is an example with no values, and the only
  declaration a parameterless command needs. The opaque commands (`browse_eval`'s JavaScript,
  the free-text commands' prose — `CommandParser.RAW_PARAM_COMMANDS` / `FREE_TEXT_COMMANDS`)
  take their one payload verbatim, so an example of theirs is not held to the canon.

A name that is not in `Commands.command_names()` is prose (`` `command(argument)` ``) and is
ignored; a name without parentheses is a mention and is never a call.
"""
import re
from dataclasses import dataclass, field
from typing import Iterable

from assistant.commands.CommandParser import CommandParser
from common.Paths import REPO_DIR, RES_DIR
from common.logging import error, info, trace

PROMPT_FILE = RES_DIR / "prompt.md"
# A backticked call: the name, then everything up to the last `)` before the closing backtick.
_CALL_RE = re.compile(r"`([a-z_][a-z0-9_]*)\(([^`]*)\)`")
_DECLARED_PARAM_RE = re.compile(r"\A([a-z_][a-z0-9_]*): (.+?)(?: = (.+))?\Z", re.S)
_BARE_VALUE_RE = re.compile(r"-?\d+(\.\d+)?|true|false")
VARIADIC = "..."
_OPAQUE = CommandParser.RAW_PARAM_COMMANDS | CommandParser.FREE_TEXT_COMMANDS


@dataclass(frozen=True)
class Call:
    """A backticked `name(inner)` in a text, with where it stands (for a rewriter)."""
    name: str
    inner: str
    start: int
    end: int


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str
    # The default as written (`""`, `false`, `"relevance"`); None for a required parameter.
    default: str | None = None


@dataclass(frozen=True)
class Declaration:
    command: str
    params: tuple[Parameter, ...]
    # `...` after the last parameter: that parameter takes any number of values.
    variadic: bool = False

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.params]


@dataclass(frozen=True)
class Example:
    command: str
    # The values as the runtime parser reads them (positional strings).
    values: list[str]
    # Why the example is not usable — does not parse, or not in the canonical form — else None.
    problem: str | None = None


@dataclass(frozen=True)
class Value:
    """One argument of a canonical example as written: `text` is its spelling inside the
    delimiters (a string's escapes unresolved, so the Keep format renders it back byte for
    byte), `kind` how it was written — `string` (double quotes), `block` (a triple-quoted
    block), `bare` (a number or boolean), `raw` (an opaque command's whole payload)."""
    text: str
    kind: str

    @property
    def value(self) -> str:
        """The value as the runtime parser reads it: a string's `\\"`, `\\'`, `` \\` `` and
        `\\\\` resolved; anything else as written."""
        if self.kind != "string":
            return self.text
        return (self.text.replace("\\\\", "\x00").replace('\\"', '"').replace("\\'", "'")
                .replace("\\`", "`").replace("\x00", "\\"))


@dataclass
class Scan:
    declarations: list[Declaration] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)


def calls(text: str) -> list[Call]:
    """Every backticked `name(...)` of the text, in order, whatever the name."""
    return [Call(m.group(1), m.group(2), m.start(), m.end()) for m in _CALL_RE.finditer(text)]


def _split_top_level(inner: str) -> list[str]:
    """The comma-separated items of a declaration, a comma inside double quotes not counting."""
    items, buf, quoted = [], [], False
    for ch in inner:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [item.strip() for item in items]


def read_declaration(call: Call) -> Declaration | None:
    """The declaration the call is, or None when its inner text is not declaration-shaped
    (an empty inner is an example with no values, not a declaration)."""
    if not call.inner.strip():
        return None
    items = _split_top_level(call.inner)
    variadic = items[-1] == VARIADIC
    if variadic:
        items = items[:-1]
    params = []
    for item in items:
        match = _DECLARED_PARAM_RE.match(item)
        if match is None:
            return None
        params.append(Parameter(match.group(1), match.group(2).strip(), match.group(3)))
    if variadic and not params:
        return None
    return Declaration(call.name, tuple(params), variadic)


def canonical_problem(inner: str) -> str | None:
    """Why the argument text of an example is not in the canonical form, or None when it is:
    values separated by commas, each a double-quoted string (or a triple-quoted block), a number or
    a boolean, nothing else."""
    if not inner.strip():
        return None
    i, n = 0, len(inner)
    while True:
        while i < n and inner[i] == " ":
            i += 1
        if inner.startswith('"""', i):
            end = inner.find('"""', i + 3)
            if end < 0:
                return "has an unterminated triple quote"
            i = end + 3
        elif i < n and inner[i] == '"':
            i += 1
            while i < n and inner[i] != '"':
                i += 2 if inner[i] == "\\" else 1
            if i >= n:
                return "has an unterminated string"
            i += 1
        else:
            match = _BARE_VALUE_RE.match(inner, i)
            if match is None or match.end() < n and inner[match.end()] not in " ,":
                token = inner[i:].split(",")[0].strip()
                if not token:
                    return "has an empty value (a trailing comma?)"
                if token[0] in ("'", "`"):
                    return f"quotes {token} with {token[0]} — strings are in double quotes"
                return f"passes the bare value {token!r} — a string is in double quotes"
            i = match.end()
        while i < n and inner[i] == " ":
            i += 1
        if i >= n:
            return None
        if inner[i] != ",":
            return f"has stray text after a value: {inner[i:]!r}"
        i += 1


def read_example(call: Call) -> Example:
    """The call read as the runtime reads it; `problem` set when the runtime cannot, or when
    the call is not written in the canonical form."""
    if call.name in _OPAQUE:
        return Example(call.name, [call.inner])
    result, _ = CommandParser([call.name]).extract_command_and_params(f"{call.name}({call.inner})")
    if result is None:
        return Example(call.name, [], "does not parse")
    return Example(call.name, result[1], canonical_problem(call.inner))


def read_values(call: Call) -> list[Value] | None:
    """The arguments of a canonical example with their spelling kept (`Value`), or None when
    the call is not canonical — a rewriter then leaves it as written. An opaque command's
    payload is one `raw` value; an empty inner is no values."""
    if call.name in _OPAQUE:
        return [Value(call.inner, "raw")] if call.inner else []
    inner = call.inner
    if canonical_problem(inner) is not None:
        return None
    values: list[Value] = []
    i, n = 0, len(inner)
    while i < n:
        while i < n and inner[i] in " ,":
            i += 1
        if i >= n:
            break
        if inner.startswith('"""', i):
            end = inner.find('"""', i + 3)
            values.append(Value(inner[i + 3:end], "block"))
            i = end + 3
        elif inner[i] == '"':
            j = i + 1
            while inner[j] != '"':
                j += 2 if inner[j] == "\\" else 1
            values.append(Value(inner[i + 1:j], "string"))
            i = j + 1
        else:
            match = _BARE_VALUE_RE.match(inner, i)
            values.append(Value(match.group(0), "bare"))
            i = match.end()
    return values


def scan(text: str, names: Iterable[str]) -> Scan:
    """The declarations and examples of the known commands in one text, in order of appearance."""
    known = set(names)
    result = Scan()
    for call in calls(text):
        if call.name not in known:
            continue
        declaration = read_declaration(call)
        if declaration is not None:
            result.declarations.append(declaration)
        else:
            result.examples.append(read_example(call))
    return result


def texts() -> dict[str, str]:
    """The texts the LLM reads commands from — the prompt template and every toolkit — by
    their path in the repository; a toolkit of a local integration (`extra_integration_dirs`,
    outside the checkout) under its full path."""
    from assistant.commands.Toolkits import TOOLKITS
    found = {str(PROMPT_FILE.relative_to(REPO_DIR)): PROMPT_FILE.read_text(encoding="utf-8")}
    for toolkit in TOOLKITS:
        path = toolkit.path
        label = str(path.relative_to(REPO_DIR) if path.is_relative_to(REPO_DIR) else path)
        found[label] = toolkit.text()
    return found


def conflicts(texts: dict[str, str], names: Iterable[str]) -> list[tuple[str, str, str]]:
    """Every command declared twice with different parameters: `(command, where first, where
    differently)`. The first declaration is the one the table keeps."""
    first: dict[str, tuple[str, Declaration]] = {}
    found = []
    for label, text in texts.items():
        for declaration in scan(text, names).declarations:
            seen = first.setdefault(declaration.command, (label, declaration))
            if seen[1] != declaration:
                found.append((declaration.command, seen[0], label))
    return found


def build_table(texts: dict[str, str], names: Iterable[str]) -> dict[str, Declaration]:
    """`command → Declaration` from the texts: the typed declarations, the first of a command
    winning, then a parameterless declaration for every command written `name()` and declared
    nowhere else. A command with neither is not in the table."""
    names = list(names)
    table: dict[str, Declaration] = {}
    bare: set[str] = set()
    for text in texts.values():
        found = scan(text, names)
        for declaration in found.declarations:
            table.setdefault(declaration.command, declaration)
        bare.update(e.command for e in found.examples if not e.values and e.problem is None)
    for command in sorted(bare - set(table)):
        table[command] = Declaration(command, ())
    return table


_DECLARATIONS: dict[str, Declaration] | None = None
_TABLE: dict[str, list[str]] | None = None


def declarations(names: Iterable[str] | None = None) -> dict[str, Declaration]:
    """`command → Declaration` over the real texts, read once per process (the rewriter's
    table). A command declared twice with different parameters is logged as an error and
    keeps its first declaration."""
    global _DECLARATIONS, _TABLE
    if _DECLARATIONS is None:
        if names is None:
            from assistant.commands.Commands import Commands
            names = Commands.command_names()
        all_texts = texts()
        for command, first, other in conflicts(all_texts, names):
            error(f"Command signatures: `{command}` is declared in {first} and differently in {other}; "
                  f"the {first} declaration is used")
        found = build_table(all_texts, names)
        parameterless = sum(1 for d in found.values() if not d.params)
        info(f"Command signatures: {len(found)} commands declared in {len(all_texts)} texts, "
             f"{parameterless} without parameters")
        undeclared = sorted(set(names) - set(found))
        if undeclared:
            trace(f"Command signatures: no declaration for {', '.join(undeclared)}")
        _DECLARATIONS = found
        _TABLE = {command: d.names for command, d in found.items()}
    return _DECLARATIONS


def table(names: Iterable[str] | None = None) -> dict[str, list[str]]:
    """`command → [param names in order]` over the real texts (see `declarations`); a
    parameterless command maps to `[]`."""
    declarations(names)
    return _TABLE


def reset() -> None:
    """Forget the cached tables (tests)."""
    global _DECLARATIONS, _TABLE
    _DECLARATIONS = _TABLE = None
