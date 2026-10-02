"""Markdown laid out as rows of styled glyphs, for a tile that wants a document rather than its source.

No marker survives: a heading is drawn by weight, colour and a rule under it, never with its
`#`; `**bold**`, `*italic*`, `` `code` ``, `~~struck~~` and `[links](…)` lose their marks; a
bullet becomes a dot with a hanging indent, a quote a bar, a fence a plate, a table aligned
columns. Every source line is wrapped to the width it is given.

The sections the document is divided into — its shallowest heading level that occurs more
than once — each take an accent from the palette in turn; their bullets, bars and sub-headings
carry it, so a screenful shows where one section ends and the next begins. `Document.headings`
is the outline, with the row each heading landed on.

A glyph remembers where in its source line it came from, so a caller can light a character
range of the source (an inline diff) on the laid-out rows.
"""
import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color, TUIScreen
from interfaces.console.framework.TextWidth import char_width, chunks, normalize

# Yellow is the band's, orange a vaulted value's, red and green a diff's.
ACCENTS = (Theme.COLOR_CYAN, Theme.COLOR_PURPLE, Theme.COLOR_TEAL, Theme.COLOR_PINK, Theme.COLOR_BLUE)

COLOR_NORMAL = Theme.COLOR_TEXT
COLOR_BOLD = Theme.COLOR_BRIGHT
COLOR_CODE = Theme.COLOR_TEAL          # teal, so a backticked name never reads as a vaulted value
COLOR_CODE_BLOCK = Theme.mix(Theme.COLOR_LABEL, Theme.COLOR_YELLOW, 0.45)
COLOR_CODE_BG = Theme.COLOR_TITLE_BG
COLOR_LINK = Theme.COLOR_CYAN
COLOR_QUOTE = Theme.COLOR_LABEL
COLOR_STRUCK = Theme.COLOR_DIM
COLOR_RULE = Theme.COLOR_GRID
COLOR_BAND = Theme.COLOR_YELLOW
COLOR_BAND_BG = Theme.COLOR_YELLOW_DIM

BULLETS = "•◦·"
RULE_CHAR = "━"
RULE_FADE = 0.6             # the share of the width over which a heading's rule fades out
TAB_WIDTH = 4
MAX_INDENT = 12
MIN_TEXT_W = 8
MIN_COLUMN_W = 6

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
TASK = re.compile(r"^\[([ xX])\]\s+")
NUMBERED = re.compile(r"^(\s*)(\d+[.)])\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^:?-+:?$")
# "Mobile: …" at the head of a bullet: a short label that starts with a letter.
KEY = re.compile(r"[^\W\d_][^:*`\[\]]{0,31}:(?=\s|$)")
LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
URL = re.compile(r"https?://[^\s<>]+")
ESCAPABLE = "\\`*_~[]#"


class Ink(NamedTuple):
    """How a glyph is drawn."""
    color: Color
    bold: bool = False
    italic: bool = False
    underline: bool = False
    href: Optional[str] = None


class Glyph(NamedTuple):
    char: str
    ink: Ink
    src: int        # the character's index in its source line; -1 for what the layout added


class Row(NamedTuple):
    glyphs: List[Glyph]
    line: int                       # the source line it renders; -1 for spacing and bands
    fill: Optional[Color] = None    # a background across the whole width

    @property
    def blank(self) -> bool:
        return not self.glyphs and self.fill is None


@dataclass
class Heading:
    title: str
    level: int      # the markdown level; 0 for a band
    depth: int      # 0 for the sections the document is divided into, 1 for their parts; negative above them
    row: int
    line: int
    accent: Color
    until: int = 0  # the line its section ends before
    size: int = 0   # the section's characters


class Document(NamedTuple):
    rows: List[Row]
    headings: List[Heading]


NORMAL = Ink(COLOR_NORMAL)


def plain(text: str, ink: Ink) -> List[Glyph]:
    return [Glyph(ch, ink, -1) for ch in text]


def cells(glyphs: List[Glyph]) -> int:
    return sum(char_width(glyph.char) for glyph in glyphs)


def text_of(glyphs: List[Glyph]) -> str:
    return "".join(glyph.char for glyph in glyphs)


def _closes(text: str, mark: str, at: int) -> bool:
    """Whether the `mark` at `at` can end an emphasis: it follows a non-space, stands alone
    and — an underscore — is not inside a word."""
    if text[at - 1].isspace() or text[at - 1] == mark or text[at + 1:at + 2] == mark:
        return False
    return mark == "*" or not text[at + 1:at + 2].isalnum()


def _opens(text: str, mark: str, at: int) -> bool:
    """Whether the `mark` at `at` starts an emphasis that is closed further on."""
    following = text[at + 1:at + 2]
    if not following or following.isspace() or following == mark:
        return False
    if mark == "_" and at > 0 and text[at - 1].isalnum():
        return False
    return any(text[j] == mark and _closes(text, mark, j) for j in range(at + 2, len(text)))


def inline(text: str, base: Ink = NORMAL, offset: int = 0) -> List[Glyph]:
    """`text` without its inline marks, each glyph in the ink its marks gave it. `offset` is
    where `text` starts in its source line."""
    out: List[Glyph] = []
    bold = struck = False
    emphasis = ""
    i, n = 0, len(text)

    def ink() -> Ink:
        color = COLOR_STRUCK if struck else COLOR_BOLD if bold and not base.bold else base.color
        return base._replace(color=color, bold=base.bold or bold, italic=base.italic or bool(emphasis))

    def put(start: int, end: int, with_ink: Ink):
        out.extend(Glyph(text[k], with_ink, offset + k) for k in range(start, end))

    while i < n:
        ch = text[i]
        if ch == "\\" and text[i + 1:i + 2] and text[i + 1] in ESCAPABLE:
            put(i + 1, i + 2, ink())
            i += 2
            continue
        if ch == "`":
            end = text.find("`", i + 1)
            if end > i + 1:
                put(i + 1, end, base._replace(color=COLOR_CODE, bold=False))
                i = end + 1
                continue
        if text.startswith("**", i) and (bold or text.find("**", i + 2) > i + 2):
            bold = not bold
            i += 2
            continue
        if text.startswith("~~", i) and (struck or text.find("~~", i + 2) > i + 2):
            struck = not struck
            i += 2
            continue
        if ch in "*_":
            if emphasis == ch and i > 0 and _closes(text, ch, i):
                emphasis = ""
                i += 1
                continue
            if not emphasis and _opens(text, ch, i):
                emphasis = ch
                i += 1
                continue
        if ch == "[":
            link = LINK.match(text, i)
            if link:
                put(link.start(1), link.end(1), ink()._replace(color=COLOR_LINK, underline=True, href=link.group(2)))
                i = link.end()
                continue
        if ch == "h" and (i == 0 or not text[i - 1].isalnum()):
            url = URL.match(text, i)
            if url:
                end = i + len(url.group().rstrip(".,;:!?)"))
                put(i, end, ink()._replace(color=COLOR_LINK, underline=True, href=text[i:end]))
                i = end
                continue
        put(i, i + 1, ink())
        i += 1
    return out


def wrap(glyphs: List[Glyph], width: int) -> List[List[Glyph]]:
    """`glyphs` broken into rows of at most `width` cells, at spaces where there is one; a
    word longer than a row is cut."""
    width = max(1, width)
    rows: List[List[Glyph]] = []
    line: List[Glyph] = []
    used, space = 0, -1
    for glyph in glyphs:
        w = char_width(glyph.char)
        if glyph.char == " " and (used + w > width or (rows and not line)):
            if line:
                rows.append(line)
                line, used, space = [], 0, -1
            continue
        while line and used + w > width:
            if space >= 0:
                head, line = line[:space], line[space + 1:]
            else:
                head, line = line, []
            rows.append(head)
            used, space = cells(line), -1
        if glyph.char == " ":
            space = len(line)
        line.append(glyph)
        used += w
    rows.append(line)
    return rows


def _table_cells(line: str) -> List[Tuple[str, int]]:
    """A table row's cells as (text, where it starts in the line)."""
    pipes = [i for i, ch in enumerate(line) if ch == "|" and (i == 0 or line[i - 1] != "\\")]
    out = []
    for a, b in zip(pipes, pipes[1:]):
        cell = line[a + 1:b]
        out.append((cell.strip(), a + 1 + len(cell) - len(cell.lstrip())))
    return out


def _clip(glyphs: List[Glyph], width: int) -> List[Glyph]:
    """`glyphs` cut or padded to exactly `width` cells, an ellipsis where it was cut."""
    if cells(glyphs) > width:
        kept: List[Glyph] = []
        used = 0
        for glyph in glyphs:
            if used + char_width(glyph.char) > width - 1:
                break
            kept.append(glyph)
            used += char_width(glyph.char)
        glyphs = kept + [Glyph("…", Ink(Theme.COLOR_DIM), -1)]
    return glyphs + plain(" " * (width - cells(glyphs)), NORMAL)


class _Table(NamedTuple):
    end: int
    widths: List[int]
    headers: FrozenSet[int]
    rules: FrozenSet[int]


class _Layout:
    def __init__(self, lines: List[str], width: int, removed: FrozenSet[int], bands: Dict[int, Tuple[str, str]]):
        self.lines = [normalize(line).expandtabs(TAB_WIDTH) for line in lines]
        self.width = max(MIN_TEXT_W, width)
        self.removed = removed
        self.bands = bands
        self.rows: List[Row] = []
        self.headings: List[Heading] = []
        self.top = self._top_level()
        self.accent = ACCENTS[0]
        self.sections = 0
        self.code = False
        self.after_heading = False
        self.table: Optional[_Table] = None

    def _top_level(self) -> int:
        """The level of the headings that divide the document: the shallowest that occurs
        more than once, else the shallowest there is."""
        counts: Dict[int, int] = {}
        code = False
        for i, line in enumerate(self.lines):
            if i in self.removed:
                continue
            if FENCE.match(line):
                code = not code
            match = None if code else HEADING.match(line)
            if match:
                counts[len(match.group(1))] = counts.get(len(match.group(1)), 0) + 1
        repeated = [level for level, count in counts.items() if count > 1]
        return min(repeated or counts or [1])

    def emit(self, glyphs: List[Glyph], line: int, fill: Optional[Color] = None):
        self.rows.append(Row(glyphs, line, fill))

    def space(self):
        if self.rows and not self.rows[-1].blank:
            self.emit([], -1)

    def block(self, line: int, lead: List[Glyph], body: List[Glyph]):
        """`body` wrapped beside `lead`, the rows after the first indented to match."""
        indent = cells(lead)
        for k, piece in enumerate(wrap(body, max(MIN_TEXT_W, self.width - indent))):
            self.emit((lead if k == 0 else plain(" " * indent, NORMAL)) + piece, line)

    def run(self) -> Document:
        for i, raw in enumerate(self.lines):
            if i in self.bands:
                self.band(i, self.bands[i])
            ghost = i in self.removed   # a line the diff shows but the document no longer has
            if not ghost and FENCE.match(raw):
                self.code = not self.code
                continue
            if self.code and not ghost:
                self.code_line(i, raw)
                continue
            if not raw.strip():
                if not self.after_heading:
                    self.space()
                continue
            heading = HEADING.match(raw)
            if heading:
                self.heading(i, heading, ghost)
                continue
            self.after_heading = False
            self.line(i, raw)
        self._measure()
        return Document(self.rows, self.headings)

    def line(self, i: int, raw: str):
        if RULE.match(raw):
            self.emit(plain("─" * self.width, Ink(COLOR_RULE)), i)
        elif TABLE_ROW.match(raw):
            self.table_row(i, raw)
        elif match := BULLET.match(raw):
            self.bullet(i, match)
        elif match := NUMBERED.match(raw):
            indent = min(MAX_INDENT, len(match.group(1)))
            lead = plain(" " * indent, NORMAL) + plain(match.group(2) + " ", Ink(self.accent, bold=True))
            self.block(i, lead, inline(match.group(3), NORMAL, match.start(3)))
        elif match := QUOTE.match(raw):
            lead = plain("▎ ", Ink(Theme.mix(Theme.COLOR_PANEL_BG, self.accent, 0.6)))
            self.block(i, lead, inline(match.group(1), Ink(COLOR_QUOTE, italic=True), match.start(1)))
        else:
            indent = min(MAX_INDENT, len(raw) - len(raw.lstrip()))
            self.block(i, plain(" " * indent, NORMAL), inline(raw.strip(), NORMAL, len(raw) - len(raw.lstrip())))

    def bullet(self, i: int, match: re.Match):
        indent = min(MAX_INDENT, len(match.group(1)))
        text, offset = match.group(2), match.start(2)
        mark, mark_ink = BULLETS[min(len(BULLETS) - 1, indent // 2)], Ink(self.accent, bold=True)
        task = TASK.match(text)
        if task:
            done = task.group(1) != " "
            mark, mark_ink = ("✓", Ink(Theme.COLOR_GREEN, bold=True)) if done else ("○", Ink(Theme.COLOR_DIM))
            text, offset = text[task.end():], offset + task.end()
        body = inline(text, NORMAL, offset)
        key = KEY.match(text)
        if key:
            key_ink = Ink(Theme.mix(COLOR_BOLD, self.accent, 0.45))
            body = [glyph._replace(ink=key_ink) if glyph.ink == NORMAL and 0 <= glyph.src < offset + key.end() else glyph
                    for glyph in body]
        self.block(i, plain(" " * indent, NORMAL) + plain(mark + " ", mark_ink), body)

    def heading(self, i: int, match: re.Match, ghost: bool):
        level = len(match.group(1))
        depth = level - self.top
        if depth == 0 and not ghost:
            self.accent = ACCENTS[self.sections % len(ACCENTS)]
            self.sections += 1
        offset = match.start(2)
        if not self.after_heading:      # a part's heading sits right under its section's rule
            self.space()
        row = len(self.rows)
        if depth <= 0:
            color = self.accent if depth == 0 else COLOR_BOLD
            for piece in wrap(inline(match.group(2), Ink(color, bold=True), offset), self.width):
                self.emit(piece, i)
            fade = max(1.0, self.width * RULE_FADE)
            self.emit([Glyph(RULE_CHAR, Ink(Theme.mix(color, COLOR_RULE, k / fade)), -1) for k in range(self.width)], i)
        elif depth == 1:
            self.block(i, plain("▌ ", Ink(self.accent)), inline(match.group(2), Ink(COLOR_BOLD, bold=True), offset))
        elif depth == 2:
            self.block(i, [], inline(match.group(2), Ink(Theme.mix(COLOR_NORMAL, self.accent, 0.6), bold=True), offset))
        else:
            self.block(i, [], inline(match.group(2), Ink(Theme.COLOR_TITLE, bold=True, italic=True), offset))
        self.after_heading = True
        if not ghost:
            title = text_of(inline(match.group(2)))
            self.headings.append(Heading(title, level, depth, row, i, self.accent if depth >= 0 else COLOR_BOLD))

    def band(self, i: int, band: Tuple[str, str]):
        """A titled strip across the document, opening a part of it that is not a section of its own making."""
        title, detail = band
        self.space()
        self.accent = COLOR_BAND
        self.headings.append(Heading(title, 0, 0, len(self.rows), i, COLOR_BAND))
        glyphs = plain(" " + title, Ink(COLOR_BAND, bold=True))
        if detail:
            glyphs += plain(" · " + detail, Ink(Theme.mix(COLOR_BAND_BG, COLOR_BAND, 0.7)))
        self.emit(glyphs, -1, COLOR_BAND_BG)
        self.after_heading = True

    def code_line(self, i: int, raw: str):
        ink = Ink(COLOR_CODE_BLOCK)
        at = 0
        for piece in chunks(raw, max(MIN_TEXT_W, self.width - 2)):
            self.emit(plain(" ", ink) + [Glyph(ch, ink, at + k) for k, ch in enumerate(piece)], i, COLOR_CODE_BG)
            at += len(piece)
        self.after_heading = False

    def _table_from(self, start: int) -> _Table:
        end = start
        while end < len(self.lines) and TABLE_ROW.match(self.lines[end]) and (end == start or end not in self.bands):
            end += 1
        rows = {i: _table_cells(self.lines[i]) for i in range(start, end)}
        rules = frozenset(i for i, row in rows.items() if row and all(TABLE_RULE.match(text) for text, _ in row))
        widths = [0] * max(len(row) for row in rows.values())
        for i, row in rows.items():
            if i not in rules:
                for column, (text, _) in enumerate(row):
                    widths[column] = max(widths[column], cells(inline(text)))
        while sum(widths) + 3 * (len(widths) - 1) > self.width and max(widths) > MIN_COLUMN_W:
            widths[widths.index(max(widths))] -= 1
        return _Table(end, widths, frozenset(i - 1 for i in rules), rules)

    def table_row(self, i: int, raw: str):
        if self.table is None or i >= self.table.end:
            self.table = self._table_from(i)
        table = self.table
        grid = Ink(COLOR_RULE)
        if i in table.rules:
            self.emit(plain("─┼─".join("─" * width for width in table.widths), grid), i)
            return
        ink = Ink(Theme.COLOR_TITLE, bold=True) if i in table.headers else NORMAL
        row = _table_cells(raw)
        glyphs: List[Glyph] = []
        for column, width in enumerate(table.widths):
            if column:
                glyphs += plain(" │ ", grid)
            text, offset = row[column] if column < len(row) else ("", 0)
            glyphs += _clip(inline(text, ink, offset), width)
        self.emit(glyphs, i)

    def _measure(self):
        """Each heading's section: up to the next heading at its level or above."""
        sizes = [0 if i in self.removed else len(line) for i, line in enumerate(self.lines)]
        for k, heading in enumerate(self.headings):
            after = (other.line for other in self.headings[k + 1:] if other.level <= heading.level)
            heading.until = next(after, len(self.lines))
            heading.size = sum(sizes[heading.line:heading.until])


def layout(lines: List[str], width: int, removed: FrozenSet[int] = frozenset(),
           bands: Optional[Dict[int, Tuple[str, str]]] = None) -> Document:
    """`lines` of markdown as rows `width` cells wide. `removed` are the lines a diff shows
    that the document no longer has: drawn, but no part of its structure. `bands` puts a
    titled strip before a line: its index → (title, the detail after it)."""
    return _Layout(lines, width, removed, bands or {}).run()


def draw_row(screen: TUIScreen, x: int, y: int, width: int, row: Row,
             tint: Optional[Callable[[Color], Color]] = None,
             lit: Optional[List[Tuple[int, int]]] = None, lit_bg: Optional[Color] = None, fill: bool = True):
    """One laid-out row at (x, y), cut at `width`. `tint` recolours every glyph (a diff's
    added or removed line); the glyphs that came from the `lit` character ranges of the
    source line stand on `lit_bg`; `fill` False leaves the row's own background out."""
    ground = row.fill if fill else None
    col = start = 0
    pending: List[str] = []
    current = None

    def flush():
        if not pending:
            return
        ink, on = current
        screen.reset_color()
        screen.set_color(tint(ink.color) if tint else ink.color)
        if on or ground:
            screen.set_bg_color(lit_bg if on else ground)
        if ink.bold:
            screen.bold()
        if ink.italic:
            screen.italic()
        if ink.underline:
            screen.underline()
        screen.link(ink.href)
        screen.write_at(x + start, y, "".join(pending))
        pending.clear()

    for glyph in row.glyphs:
        w = char_width(glyph.char)
        if col + w > width:
            break
        on = bool(lit) and glyph.src >= 0 and any(s <= glyph.src < e for s, e in lit)
        if (glyph.ink, on) != current:
            flush()
            current, start = (glyph.ink, on), col
        pending.append(glyph.char)
        col += w
    flush()
    screen.reset_color()
    if ground and col < width:
        screen.set_bg_color(ground)
        screen.write_at(x + col, y, " " * (width - col))
        screen.reset_color()
