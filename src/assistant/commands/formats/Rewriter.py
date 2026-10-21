"""Renders the command calls of a prompt text in the active command format, leaving everything else as written.

The texts (`res/prompt.md`, the toolkits) are written in Keep's syntax; `rewrite` finds every
backticked `name(...)` whose name is in the signature table and replaces it with the format's
rendering — `render_declaration` for a declaration, `render_example` for an example, whose
values are paired with the declared names in order. A name without parentheses is a mention and
stays; `name()` of a command the table gives parameters is a mention too (the toolkits mention
commands that way) and is rendered by `render_mention` — the bare name in every syntax but
Keep's, whose instructions say a name without parentheses never runs; a `word(x)` that is no
command and a call the canon cannot read are left as written rather than guessed at. For the
Keep format the rewrite is the identity.
"""
from .CommandFormat import CommandFormat
from .Signatures import Declaration, calls, read_declaration, read_values
from common.logging import trace


def rewrite(text: str, fmt: CommandFormat, table: dict[str, Declaration]) -> str:
    """`text` with every declaration and example of a command in `table` rendered by `fmt`."""
    pieces, at = [], 0
    rendered, left = 0, []
    for call in calls(text):
        declaration = table.get(call.name)
        if declaration is None:
            continue
        own = read_declaration(call)
        if own is not None:
            replacement = fmt.render_declaration(own)
        else:
            if not call.inner and declaration.params:
                replacement = fmt.render_mention(call.name)
                if replacement == text[call.start:call.end]:
                    continue
                pieces.append(text[at:call.start])
                pieces.append(replacement)
                at = call.end
                rendered += 1
                continue
            values = read_values(call)
            if values is None or (len(values) > len(declaration.params) and not declaration.variadic):
                left.append(call.name)
                continue
            replacement = fmt.render_example(call.name, values, declaration)
        pieces.append(text[at:call.start])
        pieces.append(replacement)
        at = call.end
        rendered += 1
    if left:
        trace(f"Command rewriter ({fmt.name}): {len(left)} call(s) left as written, not canonical or "
              f"too many values: {', '.join(sorted(set(left)))}")
    if not rendered:
        return text
    pieces.append(text[at:])
    return "".join(pieces)
