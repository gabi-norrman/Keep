"""Parse the Telegram-supported HTML subset into styled spans for the TUI.

Telegram's bot API accepts a small HTML subset; it is the rich-text dialect every chat
interface speaks (see interfaces/chat/RichText), so the TUI's chat pane receives it too.
We parse the markup into flat StyledSpans so the TUI can render styling (bold, italic,
underline, color tints for code/links/quotes/etc.) instead of showing the raw `<b>`
markers.

Tags handled (per https://core.telegram.org/bots/api#html-style):
    <b> <strong>                                  → bold
    <i> <em>                                       → italic
    <u> <ins>                                      → underline
    <s> <strike> <del>                             → strikethrough
    <a href="URL">text</a>                         → link (carries href)
    <code>                                         → inline code
    <pre> / <pre><code class="language-...">       → code block
    <blockquote>                                   → block quote
    <tg-spoiler> / <span class="tg-spoiler">       → spoiler
    <span class="...">                             → the class on the span, for a renderer's own styling
    <tg-emoji emoji-id="...">X</tg-emoji>          → just emit X

Unknown tags are silently stripped (their inner text is kept). HTML entities
(`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`, ...) are decoded automatically. A bare
`https://…` in prose (outside `<a>`, `<code>` and `<pre>`) becomes a link to itself.
"""

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Callable, List, Optional, Tuple

from interfaces.console.framework.TUIScreen import Color
from interfaces.console.framework.TextWidth import char_width, normalize

# A bare URL in prose becomes a link of its own text, so the terminal opens it on a click even
# when the wrap cuts it over several rows (each row's run carries the whole href). Trailing
# punctuation belongs to the sentence, not the URL.
URL = re.compile(r"https?://[^\s<>]+")
_URL_TRAIL = ".,;:!?)\"'"


@dataclass
class StyledSpan:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False     # inline <code>
    pre: bool = False      # block <pre> / <pre><code>
    quote: bool = False    # inside <blockquote>
    spoiler: bool = False
    href: Optional[str] = None
    cls: Optional[str] = None     # the class of an enclosing <span>, for a renderer's own styling
    fg: Optional[Color] = None    # an explicit colour, set by renderers that build spans themselves (diffs)
    bg: Optional[Color] = None


_BOLD_TAGS = {"b", "strong"}
_ITALIC_TAGS = {"i", "em"}
_UNDERLINE_TAGS = {"u", "ins"}
_STRIKE_TAGS = {"s", "strike", "del"}


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.spans: List[StyledSpan] = []
        # Each stack entry is (tag_name, undo_callable). Storing the undo lets us
        # cleanly reverse arbitrary state mutations (counter inc, href set) and
        # tolerates malformed nesting / unclosed tags via close().
        self._stack: List[Tuple[str, Callable[[], None]]] = []

        # Counter-style nesting so <b><b>x</b>y</b> still bolds y after one </b>.
        self._bold = 0
        self._italic = 0
        self._underline = 0
        self._strike = 0
        self._code = 0
        self._pre = 0
        self._quote = 0
        self._spoiler = 0
        self._href: Optional[str] = None
        self._cls: Optional[str] = None
        self._pre_opened = False   # the next data is the first inside a <pre>

    def _close_pre(self):
        """As in HTML, the newline right after `<pre>` and the one right before `</pre>`
        are part of the markup, not the block."""
        self._dec("_pre")
        self._pre_opened = False
        if self.spans and self.spans[-1].pre and self.spans[-1].text.endswith("\n"):
            self.spans[-1] = replace(self.spans[-1], text=self.spans[-1].text[:-1])

    def _push(self, tag: str, undo: Callable[[], None]):
        self._stack.append((tag, undo))

    def _inc(self, attr: str):
        v = getattr(self, attr)
        setattr(self, attr, v + 1)

    def _dec(self, attr: str):
        v = getattr(self, attr)
        setattr(self, attr, max(0, v - 1))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_dict = dict(attrs) if attrs else {}

        if tag in _BOLD_TAGS:
            self._inc("_bold"); self._push(tag, lambda: self._dec("_bold"))
        elif tag in _ITALIC_TAGS:
            self._inc("_italic"); self._push(tag, lambda: self._dec("_italic"))
        elif tag in _UNDERLINE_TAGS:
            self._inc("_underline"); self._push(tag, lambda: self._dec("_underline"))
        elif tag in _STRIKE_TAGS:
            self._inc("_strike"); self._push(tag, lambda: self._dec("_strike"))
        elif tag == "code":
            self._inc("_code"); self._push(tag, lambda: self._dec("_code"))
        elif tag == "pre":
            self._inc("_pre"); self._push(tag, self._close_pre)
            self._pre_opened = True
        elif tag == "blockquote":
            self._inc("_quote"); self._push(tag, lambda: self._dec("_quote"))
        elif tag == "tg-spoiler":
            self._inc("_spoiler"); self._push(tag, lambda: self._dec("_spoiler"))
        elif tag == "span" and attr_dict.get("class") == "tg-spoiler":
            self._inc("_spoiler"); self._push(tag, lambda: self._dec("_spoiler"))
        elif tag == "span":
            old_cls = self._cls
            self._cls = attr_dict.get("class")

            def undo():
                self._cls = old_cls
            self._push(tag, undo)
        elif tag == "a":
            old_href = self._href
            self._href = attr_dict.get("href")

            def undo():
                self._href = old_href
            self._push(tag, undo)
        elif tag == "tg-emoji":
            # Just unwrap — inner text is the emoji.
            self._push(tag, lambda: None)
        else:
            # Unknown / unsupported tag: still track on stack so its end tag matches,
            # but apply no styling (silently strip the wrapper).
            self._push(tag, lambda: None)

    def handle_endtag(self, tag):
        tag = tag.lower()
        # Pop everything down to (and including) the matching open. Tolerates
        # interleaved/malformed tags by unwinding intermediate entries.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                while len(self._stack) > i:
                    _, undo = self._stack.pop()
                    undo()
                return
        # Unmatched end tag — ignore quietly.

    def handle_data(self, data):
        if self._pre_opened:
            self._pre_opened = False
            data = data[1:] if data.startswith("\n") else data
        if not data:
            return
        if self._href is None and not self._code and not self._pre:
            for text, href in _autolink(data):
                self._emit(text, href)
        else:
            self._emit(data, self._href)

    def _emit(self, text: str, href: Optional[str]):
        self.spans.append(StyledSpan(
            text=text,
            bold=self._bold > 0,
            italic=self._italic > 0,
            underline=self._underline > 0,
            strike=self._strike > 0,
            code=self._code > 0,
            pre=self._pre > 0,
            quote=self._quote > 0,
            spoiler=self._spoiler > 0,
            href=href,
            cls=self._cls,
        ))

    def close(self):
        super().close()
        # Auto-close any tags the LLM forgot.
        while self._stack:
            _, undo = self._stack.pop()
            undo()


def _autolink(text: str) -> List[Tuple[str, Optional[str]]]:
    """`text` cut into (text, href) pieces: the bare URLs link to themselves, the rest is plain."""
    pieces: List[Tuple[str, Optional[str]]] = []
    at = 0
    for match in URL.finditer(text):
        url = match.group().rstrip(_URL_TRAIL)
        if url.endswith("://"):
            continue
        if match.start() > at:
            pieces.append((text[at:match.start()], None))
        pieces.append((url, url))
        at = match.start() + len(url)
    if at < len(text):
        pieces.append((text[at:], None))
    return pieces


def parse(html: str) -> List[StyledSpan]:
    """Parse a Telegram-HTML string into a flat list of StyledSpans."""
    p = _Parser()
    p.feed(html)
    p.close()
    return p.spans


def split_paragraphs(spans: List[StyledSpan]) -> List[List[StyledSpan]]:
    """Split spans on '\\n' boundaries; returns list of paragraphs (each a span list)."""
    paragraphs: List[List[StyledSpan]] = [[]]
    for span in spans:
        if "\n" not in span.text:
            if span.text:
                paragraphs[-1].append(span)
            continue
        parts = span.text.split("\n")
        for i, part in enumerate(parts):
            if part:
                paragraphs[-1].append(replace(span, text=part))
            if i < len(parts) - 1:
                paragraphs.append([])
    return paragraphs


def _coalesce(chars: List[Tuple[str, StyledSpan]]) -> List[StyledSpan]:
    """Group a list of (char, style) into spans of consecutive same-style chars."""
    if not chars:
        return []
    out: List[StyledSpan] = []
    i = 0
    while i < len(chars):
        cur_style = chars[i][1]
        j = i
        text = []
        while j < len(chars) and chars[j][1] is cur_style:
            text.append(chars[j][0])
            j += 1
        out.append(replace(cur_style, text="".join(text)))
        i = j
    return out


def _cut_cells(chars: List[Tuple[str, StyledSpan]], width: int) -> List[List[StyledSpan]]:
    """`chars` in pieces of at most `width` cells, cut on cell boundaries, spaces kept."""
    out: List[List[StyledSpan]] = []
    piece: List[Tuple[str, StyledSpan]] = []
    used = 0
    for ch, style in chars:
        w = char_width(ch)
        if piece and used + w > width:
            out.append(_coalesce(piece))
            piece, used = [], 0
        piece.append((ch, style))
        used += w
    out.append(_coalesce(piece))
    return out


def wrap_spans(spans: List[StyledSpan], width: int) -> List[List[StyledSpan]]:
    """Word-wrap a flat list of styled spans into display lines.

    '\\n' inside span text is treated as a hard line break (preserves blank
    paragraph lines). Within each paragraph, words are broken on whitespace and
    re-flowed to fit `width`. Words longer than `width` are hard-split.

    Returns a list of lines, where each line is a list of StyledSpans (style
    boundaries preserved through wrap cuts).
    """
    if width <= 0:
        return [[]]

    lines: List[List[StyledSpan]] = []

    for paragraph in split_paragraphs(spans):
        if not paragraph:
            lines.append([])
            continue

        # Flatten paragraph to (char, style); spaces collapse via word-flush below.
        chars: List[Tuple[str, StyledSpan]] = []
        for span in paragraph:
            for ch in normalize(span.text):
                chars.append((ch, span))

        if all(span.pre for span in paragraph):
            # A code block is laid out by its author — box-drawn tables, aligned columns —
            # so its spaces stay and a line too long is cut at the width, never reflowed.
            lines.extend(_cut_cells(chars, width))
            continue

        cur_line: List[Tuple[str, StyledSpan]] = []
        cur_word: List[Tuple[str, StyledSpan]] = []
        line_w = word_w = 0   # in cells
        # The style of the whitespace before the word being collected. The space put back
        # in front of the word keeps it, so the space before a link or an underlined word
        # is not underlined along with it.
        space_style: Optional[StyledSpan] = None

        def flush_word():
            nonlocal cur_line, cur_word, line_w, word_w
            if not cur_word:
                return
            need_space = 1 if cur_line else 0
            if line_w + need_space + word_w <= width:
                if cur_line:
                    cur_line.append((" ", space_style or cur_word[0][1]))
                cur_line.extend(cur_word)
                line_w += need_space + word_w
                cur_word, word_w = [], 0
                return
            # Doesn't fit on current line.
            if cur_line:
                lines.append(_coalesce(cur_line))
                cur_line, line_w = [], 0
            # Word longer than width → hard-split into width-sized chunks (never inside a glyph).
            while word_w > width:
                cut = used = 0
                while cut < len(cur_word) and used + char_width(cur_word[cut][0]) <= width:
                    used += char_width(cur_word[cut][0])
                    cut += 1
                cut = max(1, cut)
                lines.append(_coalesce(cur_word[:cut]))
                cur_word = cur_word[cut:]
                word_w = sum(char_width(ch) for ch, _ in cur_word)
            cur_line, line_w = cur_word, word_w
            cur_word, word_w = [], 0

        for ch, style in chars:
            if ch == " " or ch == "\t":
                flush_word()
                space_style = style
            else:
                cur_word.append((ch, style))
                word_w += char_width(ch)

        flush_word()
        lines.append(_coalesce(cur_line))

    return lines
