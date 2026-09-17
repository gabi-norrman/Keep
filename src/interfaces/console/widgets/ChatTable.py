"""Markdown pipe tables in the chat pane, drawn as aligned columns (`plans/CHAT_TABLES_PLAN.md`).

The reply reaches the pane as the chat HTML dialect, which has no table tag, so a table is
found on the text: a run of consecutive lines that start with `|`. A trailing `|` is not
required, so a row the typewriter is still revealing counts and the table does not change
shape while it streams. The separator row (`|---|:--:|`) becomes a rule under the header and
is what makes the first row bold; a table without one is drawn without a header. A lone
pipe inside prose starts no table, and nothing inside a `<pre>` block does: a code block is
laid out by its author, so its lines are prose whatever they start with.

Cells are parsed as the same HTML the rest of the reply is, so `<b>` and `<code>` inside a
cell keep their styling. Widths are measured in cells (`TextWidth`), never `len()`. A table
wider than the pane wraps cells inside their columns: the widest columns give up width first,
down to `MIN_COLUMN`, and a row whose cells wrap takes several display lines.
"""
import re
from dataclasses import dataclass, replace
from typing import List, Optional, Union

from common.logging import trace
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.framework.TextWidth import display_width
from interfaces.console.tabs.TelegramHtmlParser import StyledSpan, parse as parse_html, wrap_spans

MIN_COLUMN = 6
GUTTER = " │ "
RULE_JOINT = "─┼─"

ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT = "left", "center", "right"

_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_PRE_TAG = re.compile(r"<(/?)pre\b", re.IGNORECASE)
_ESCAPED_PIPE = "\x00"


@dataclass
class Table:
    rows: List[List[str]]              # cell HTML per row; the header row first when `header`
    header: bool = False
    aligns: Optional[List[str]] = None  # from the separator row, per column

    @property
    def columns(self) -> int:
        return max(len(row) for row in self.rows) if self.rows else 0


Block = Union[str, Table]


def split_blocks(text: str) -> List[Block]:
    """`text` as alternating prose and `Table` blocks. Prose keeps its line breaks; a
    prose block is never empty (a blank line between two tables is a one-line block)."""
    blocks: List[Block] = []
    prose: List[str] = []
    rows: List[str] = []

    def flush_prose():
        if prose:
            blocks.append("\n".join(prose))
            prose.clear()

    def flush_table():
        if rows:
            blocks.append(_parse_table(rows))
            rows.clear()

    pre_depth = 0   # <pre> blocks open where the line starts; a `<pre>` still unclosed while streaming stays open
    for line in text.split("\n"):
        if pre_depth == 0 and line.lstrip().startswith("|"):
            flush_prose()
            rows.append(line)
        else:
            flush_table()
            prose.append(line)
        for tag in _PRE_TAG.finditer(line):
            pre_depth = max(0, pre_depth + (-1 if tag.group(1) else 1))
    flush_prose()
    flush_table()
    if any(isinstance(b, Table) for b in blocks):
        trace(f"ChatTable: {sum(isinstance(b, Table) for b in blocks)} table(s) in a {len(text)}-char reply")
    return blocks


def _parse_table(lines: List[str]) -> Table:
    rows: List[List[str]] = []
    header = False
    aligns = None
    for i, line in enumerate(lines):
        if i == 1 and _SEPARATOR.match(line.strip()):
            header = True
            aligns = [_alignment(cell) for cell in _cells(line)]
            continue
        rows.append(_cells(line))
    return Table(rows, header, aligns)


def _cells(line: str) -> List[str]:
    inner = line.strip().replace("\\|", _ESCAPED_PIPE)
    inner = inner[1:] if inner.startswith("|") else inner
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip().replace(_ESCAPED_PIPE, "|") for c in inner.split("|")]


def _alignment(marker: str) -> str:
    left, right = marker.startswith(":"), marker.endswith(":")
    if left and right:
        return ALIGN_CENTER
    if right:
        return ALIGN_RIGHT
    return ALIGN_LEFT


def column_widths(natural: List[int], width: int) -> List[int]:
    """Column widths that fit `width` with the gutters: the natural widths when they fit,
    otherwise the widest columns shrink first, down to `MIN_COLUMN`; past that every column
    shrinks so the table still never overflows."""
    widths = [max(1, w) for w in natural]
    gutters = len(GUTTER) * (len(widths) - 1)
    floor = MIN_COLUMN
    while sum(widths) + gutters > width:
        candidates = [i for i, w in enumerate(widths) if w > floor]
        if not candidates:
            if floor == 1:
                break
            floor = 1
            continue
        widest = max(candidates, key=lambda i: widths[i])
        widths[widest] -= 1
    return widths


def table_lines(table: Table, width: int, gutter_fg: Color) -> List[List[StyledSpan]]:
    """The display lines of `table` laid out in `width` cells: the header bold above a rule,
    cells padded to their column and joined by dim gutters, a cell too wide for its column
    wrapped onto more lines of the same row."""
    ncols = table.columns
    if ncols == 0:
        return []
    aligns = (table.aligns or []) + [ALIGN_LEFT] * ncols
    rows = [[parse_html(cell) for cell in row] + [[]] * (ncols - len(row)) for row in table.rows]
    if table.header and rows:
        rows[0] = [[replace(s, bold=True) for s in spans] for spans in rows[0]]
    natural = [max(display_width("".join(s.text for s in row[c])) for row in rows) for c in range(ncols)]
    widths = column_widths(natural, width)
    gutter = StyledSpan(text=GUTTER, fg=gutter_fg)

    lines: List[List[StyledSpan]] = []
    for r, row in enumerate(rows):
        wrapped = [wrap_spans(spans, widths[c]) for c, spans in enumerate(row)]
        for i in range(max(len(w) for w in wrapped)):
            line: List[StyledSpan] = []
            for c in range(ncols):
                if c:
                    line.append(gutter)
                line.extend(_padded(wrapped[c][i] if i < len(wrapped[c]) else [], widths[c], aligns[c]))
            lines.append(line)
        if r == 0 and table.header:
            lines.append([StyledSpan(text=RULE_JOINT.join("─" * w for w in widths), fg=gutter_fg)])
    return lines


def _padded(spans: List[StyledSpan], width: int, align: str) -> List[StyledSpan]:
    pad = width - display_width("".join(s.text for s in spans))
    if pad <= 0:
        return list(spans)
    if align == ALIGN_RIGHT:
        left, right = pad, 0
    elif align == ALIGN_CENTER:
        left, right = pad // 2, pad - pad // 2
    else:
        left, right = 0, pad
    out = [StyledSpan(text=" " * left)] if left else []
    out.extend(spans)
    if right:
        out.append(StyledSpan(text=" " * right))
    return out
