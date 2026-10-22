import zlib
from typing import Dict, Optional, List, Tuple

from urllib.parse import quote

from common.logging import trace
from interfaces.console.framework import Pictures
from interfaces.console.framework.LinkProbe import LinkStatus, NO_LINK_STATUS
from interfaces.console.framework.Pictures import Picture
from interfaces.console.framework.TextWidth import _NARROW_LIMIT, _widths, char_width, drawable

# (x, y, cols, rows, first_row, visible_rows): where a picture is on screen and which band of it
Placement = Tuple[int, int, int, int, int, int]


class Color:
    __slots__ = ('r', 'g', 'b')

    def __init__(self, r: int = 0, g: int = 0, b: int = 0):
        self.r = r
        self.g = g
        self.b = b

    def __eq__(self, other):
        if other is self:
            return True
        if type(other) is not Color:
            return NotImplemented
        return self.r == other.r and self.g == other.g and self.b == other.b

    def __hash__(self):
        return hash((self.r, self.g, self.b))

    def __str__(self) -> str:
        return f"\033[38;2;{self.r};{self.g};{self.b}m"

    def bg(self) -> str:
        return f"\033[48;2;{self.r};{self.g};{self.b}m"


def _colors_equal(a, b):
    """Fast color comparison using identity first, then field comparison."""
    if a is b:
        return True
    if a is None or b is None:
        return False
    return a.r == b.r and a.g == b.g and a.b == b.b


class Style:
    """A cell's attributes. `href` makes the cell part of a clickable hyperlink (OSC 8), which
    terminals such as Ghostty, iTerm2 and kitty open on click."""
    __slots__ = ('fg', 'bg', 'bold', 'italic', 'underline', 'href')

    def __init__(self, fg: Optional[Color] = None, bg: Optional[Color] = None,
                 bold: bool = False, italic: bool = False, underline: bool = False,
                 href: Optional[str] = None):
        self.fg = fg
        self.bg = bg
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.href = href

    def __eq__(self, other):
        if other is self:
            return True
        if type(other) is not Style:
            return NotImplemented
        return (self.bold == other.bold
                and self.italic == other.italic
                and self.underline == other.underline
                and self.href == other.href
                and _colors_equal(self.fg, other.fg)
                and _colors_equal(self.bg, other.bg))

    def copy(self) -> 'Style':
        return Style(self.fg, self.bg, self.bold, self.italic, self.underline, self.href)

    def copy_from(self, other: 'Style'):
        self.fg = other.fg
        self.bg = other.bg
        self.bold = other.bold
        self.italic = other.italic
        self.underline = other.underline
        self.href = other.href

    def reset(self):
        self.fg = None
        self.bg = None
        self.bold = False
        self.italic = False
        self.underline = False
        self.href = None

    def is_default(self) -> bool:
        return not (self.fg or self.bg or self.bold or self.italic or self.underline or self.href)


class Cell:
    __slots__ = ('char', 'style')

    def __init__(self, char: str = " ", style: Style = None):
        self.char = char
        self.style = style if style is not None else Style()


class TUIScreen:
    """Double-buffered character screen that emits only the ANSI diff needed to update a terminal.

    Pictures are a layer over the cells (`image`). With `graphics` on — the client's terminal
    speaks the kitty graphics protocol — a picture is sent once (`Pictures.transmit`, re-sent
    when its box changes) and placed over blank cells; the frame carries a placement only
    when it is new or moved, and a delete when one has gone, so a still picture costs nothing
    per frame. `MAX_TRANSMITTED` pictures stay in the terminal for scrolling back; the oldest
    off-screen one is freed past that. Without `graphics` a picture is half-block pixels in
    the cells themselves (`Pixels.blit`), through the ordinary diff."""

    MAX_TRANSMITTED = 32

    # ANSI Escape sequences
    ESC = "\033"
    CSI = f"{ESC}["

    # The ground every cell sits on. Painted explicitly rather than left to the terminal's
    # default background, so the console looks the same whatever profile the terminal runs:
    # Terminal.app's stock profiles are translucent grey or white, not black.
    BACKGROUND = Color(0, 0, 0)
    BACKGROUND_SEQ = BACKGROUND.bg()

    # Cursor control
    HIDE_CURSOR = f"{CSI}?25l"
    SHOW_CURSOR = f"{CSI}?25h"
    CURSOR_HOME = f"{CSI}1;1H"

    # Screen control
    CLEAR_SCREEN = f"{CSI}2J"
    CLEAR_LINE = f"{CSI}2K"

    # Synchronized output (DECSET 2026)
    BEGIN_SYNC = f"{CSI}?2026h"
    END_SYNC = f"{CSI}?2026l"

    # Alternate screen buffer (DECSET 1049)
    ENTER_ALT_SCREEN = f"{CSI}?1049h"
    EXIT_ALT_SCREEN = f"{CSI}?1049l"
    # The kitty keyboard protocol's "disambiguate" flag: Shift+Enter, Escape, Alt and Ctrl
    # chords arrive as `CSI code;mods u` instead of the ambiguous legacy bytes (Shift+Enter
    # is otherwise a plain Enter). Pushed on the alternate screen, whose flag stack is its
    # own, and popped before it is left; a terminal without the protocol ignores both.
    PUSH_KEY_REPORTING = f"{CSI}>1u"
    POP_KEY_REPORTING = f"{CSI}<u"
    # Bracketed paste (DECSET 2004): a paste arrives between the two markers, so the server
    # can hand it over whole instead of typing its newlines as Enter.
    ENABLE_BRACKETED_PASTE = f"{CSI}?2004h"
    DISABLE_BRACKETED_PASTE = f"{CSI}?2004l"
    PASTE_START = f"{CSI}200~"
    PASTE_END = f"{CSI}201~"

    # Text attributes
    RESET = f"{CSI}0m"
    BOLD = f"{CSI}1m"
    ITALIC = f"{CSI}3m"
    UNDERLINE = f"{CSI}4m"

    # Hyperlinks (OSC 8): `LINK_OPEN(url)` … text … `LINK_CLOSE`. SGR reset does not end a
    # hyperlink, so every linked run closes its own.
    OSC = f"{ESC}]"
    ST = f"{ESC}\\"
    LINK_CLOSE = f"{OSC}8;;{ST}"

    def __init__(self, width: int = 80, height: int = 24):
        self.width = max(1, width)
        self.height = max(1, height)
        self.front_buffer = self._create_buffer()
        self.next_frame = self._create_buffer()
        self.cursor_x = 1
        self.cursor_y = 1
        self.current_style = Style()
        self.control_sequences: List[str] = []
        self.link_status: LinkStatus = NO_LINK_STATUS   # set by TUIServer before each frame: how far behind the terminal is
        self.link_probe_wanted = False   # a tab that shows link_status sets this while drawing; probes only ride frames when one did
        self.half_blocks = True   # set by TUIServer from the client's terminal: False where `▀` renders badly (Terminal.app)
        self.graphics = False     # set by TUIServer from the client's terminal: True where it speaks the kitty graphics protocol
        self.hyperlinks = False   # set by TUIServer from the client's terminal: True where a click opens an OSC 8 link
        self._placements: Dict[int, Tuple[Picture, Placement]] = {}   # the pictures of the frame being drawn, by image id
        self._shown: Dict[int, Placement] = {}                        # the pictures on the terminal's screen
        self._transmitted: Dict[int, Tuple[int, int]] = {}            # the boxes the terminal holds pictures for, oldest first

    def resize(self, width: int, height: int):
        """Resize buffers and reset state."""
        width = max(1, width)
        height = max(1, height)
        if width == self.width and height == self.height:
            return
        self.width = width
        self.height = height
        self.clear_terminal()
        self.cursor_x = 1
        self.cursor_y = 1
        self.current_style = Style()

    def clear_terminal(self):
        """Wipe the client's screen and start both buffers afresh, so the next frame is drawn
        from scratch: after a resize, or to get rid of raw ANSI output the diff cannot see."""
        self._forget_images()
        self.control_sequences.append(self.CLEAR_SCREEN)
        self.control_sequences.append(self.CURSOR_HOME)
        self.control_sequences.append(self.paint_background())
        self.front_buffer = self._create_buffer()
        self.next_frame = self._create_buffer()

    def paint_background(self) -> str:
        """Every cell of the screen as a space on the BACKGROUND: the one full paint a client
        gets when it connects, resizes or is cleared, so the cells no tab ever writes are
        ours and not the terminal's. The diff never repeats it — untouched cells stay equal."""
        blank = " " * self.width
        rows = "".join(f"{self.CSI}{y};1H{blank}" for y in range(1, self.height + 1))
        return f"{self.RESET}{self.BACKGROUND_SEQ}{rows}{self.RESET}"

    def erase_line(self, y: int) -> str:
        """The control sequence that blanks row y onto the BACKGROUND (background colour
        erase: the terminal fills the erased cells with the background in effect)."""
        return f"{self.CSI}{y};1H{self.RESET}{self.BACKGROUND_SEQ}{self.CLEAR_LINE}"

    def _create_buffer(self) -> List[List[Cell]]:
        return [[Cell() for _ in range(self.width)] for _ in range(self.height)]

    @staticmethod
    def _clamp_color_component(value: float) -> int:
        v = int(value)
        if v < 0:
            return 0
        if v > 255:
            return 255
        return v

    def invalidate_front_buffer(self):
        """Reset the front buffer so the next get_frame() emits a full redraw.

        Used when a new client connects: after the client's screen has been cleared,
        it must receive every non-empty cell, not just the cells changing this frame — and
        the empty ones as one background paint, since the diff never emits those.
        """
        self._forget_images()
        self.control_sequences.append(self.paint_background())
        self.front_buffer = self._create_buffer()

    def _forget_images(self):
        """The terminal holds no pictures any more — a new client, a resize, a wipe: take
        down whatever the previous screen showed and send everything afresh next frame."""
        if self.graphics and (self._shown or self._transmitted):
            self.control_sequences.append(Pictures.delete_all())
            trace(f"TUIScreen: dropped {len(self._transmitted)} picture(s) from the terminal for a fresh screen")
        self._shown = {}
        self._transmitted = {}

    # Buffer management
    def clear_buffer(self):
        """Prepare the back buffer for the next frame."""
        for row in self.next_frame:
            for cell in row:
                cell.char = " "
                cell.style.reset()
        self._placements = {}
        self.cursor_x = 1
        self.cursor_y = 1
        self.current_style = Style()

    def write(self, text: str):
        """Write text from the cursor to the end of its row (a `\\n` moves to the next row).
        A wide glyph takes its cell and the next, which holds "" as its continuation; a
        combining mark joins the glyph before it; a wide glyph that would hang over the
        row's end is written as a space. Overwriting either half of a wide glyph blanks the
        other half. Sequences terminals disagree on are normalized away and a control
        character (a tab, an escape) is drawn as a space — see TextWidth."""
        if not text:
            return

        cur_style = self.current_style
        next_frame = self.next_frame
        cx = self.cursor_x
        cy = self.cursor_y
        w = self.width
        h = self.height
        if not 1 <= cy <= h:
            return
        row = next_frame[cy - 1]
        widths = _widths

        for char in drawable(text):
            if char == '\n':
                cy += 1
                cx = 1
                if cy > h:
                    break
                row = next_frame[cy - 1]
                continue

            if char < _NARROW_LIMIT:
                cw = 1
            else:
                cw = widths.get(char)
                if cw is None:
                    cw = char_width(char)
                if cw == 0:
                    # A combining mark joins the glyph before it (skipping a continuation).
                    if 1 < cx <= w + 1:
                        head = row[cx - 2]
                        if head.char == "" and cx > 2:
                            head = row[cx - 3]
                        if head.char:
                            head.char += char
                    continue
                if cw == 2 and cx == w:
                    char = " "
                    cw = 1

            if 1 <= cx <= w:
                idx = cx - 1
                cell = row[idx]
                # Overwriting either half of a wide glyph blanks its other half.
                if cell.char == "" and idx > 0:
                    row[idx - 1].char = " "
                elif idx + 1 < w and row[idx + 1].char == "":
                    row[idx + 1].char = " "
                cell.char = char
                cell.style.copy_from(cur_style)
                if cw == 2:
                    tail = row[idx + 1]
                    if idx + 2 < w and row[idx + 2].char == "":
                        row[idx + 2].char = " "
                    tail.char = ""
                    tail.style.copy_from(cur_style)
            cx += cw

        self.cursor_x = min(cx, w + 1)
        self.cursor_y = cy

    def write_at(self, x: int, y: int, text: str):
        """Write text at specific position."""
        self.cursor_x = max(1, min(self.width, x))
        self.cursor_y = max(1, min(self.height, y))
        self.write(text)

    def write_raw_ansi_at(self, x: int, y: int, ansi_text: str):
        """Write raw ANSI text directly to output, bypassing the buffer system. Every reset
        inside the text lands on the BACKGROUND again, not on the terminal's default."""
        self.control_sequences.append(f"{self.CSI}{y};{x}H{self.BACKGROUND_SEQ}")
        self.control_sequences.append(ansi_text.replace("\x1b[0m", "\x1b[0m" + self.BACKGROUND_SEQ)
                                      .replace("\x1b[m", "\x1b[m" + self.BACKGROUND_SEQ))
        self.control_sequences.append(self.RESET)

    def image(self, x: int, y: int, picture: Picture, cols: int, rows: int,
              first_row: int = 0, visible_rows: Optional[int] = None):
        """Draw `picture` fitted to a `cols` × `rows` box with its top-left cell at (x, y) — or,
        with `first_row` and `visible_rows`, only that band of the box's rows, at (x, y): a
        picture partly scrolled out of a pane. The box is clipped to the screen."""
        visible = rows - first_row if visible_rows is None else visible_rows
        visible = min(visible, self.height - y + 1)
        cols = min(cols, self.width - x + 1)
        if visible <= 0 or cols <= 0 or not 1 <= y <= self.height or not 1 <= x <= self.width:
            return
        if not self.graphics:
            from interfaces.console.framework import Pixels
            surface = picture.pixels(cols, rows)[2 * first_row:2 * (first_row + visible)]
            Pixels.blit(self, x, y, surface)
            return
        blank = " " * cols
        self.current_style = Style()
        for row in range(visible):
            self.write_at(x, y + row, blank)
        self._placements[picture.id] = (picture, (x, y, cols, rows, first_row, visible))

    def _image_commands(self) -> Tuple[List[str], List[str]]:
        """What this frame's pictures need from the terminal: (deletes, transmits and
        placements). A picture shown where it was costs nothing; one that moved or whose
        visible band changed is taken down and placed again; one off the screen is taken
        down and kept for a later frame, until MAX_TRANSMITTED others have come after it."""
        before: List[str] = []
        after: List[str] = []
        for image_id, shown in self._shown.items():
            placed = self._placements.get(image_id)
            if placed is None or placed[1] != shown:
                before.append(Pictures.delete_placements(image_id))
        for image_id, (picture, placement) in self._placements.items():
            if self._shown.get(image_id) == placement:
                continue
            x, y, cols, rows, first_row, visible = placement
            png, png_size = picture.png(cols, rows)
            if self._transmitted.get(image_id) != (cols, rows):
                after.append(Pictures.transmit(image_id, png))
                trace(f"TUIScreen: sending picture {image_id} ({len(png) // 1024} kB) for a {cols}x{rows} box")
            self._transmitted.pop(image_id, None)
            self._transmitted[image_id] = (cols, rows)
            after.append(f"{self.CSI}{y};{x}H" + Pictures.place(image_id, cols, rows, png_size, first_row, visible))
        for image_id in [i for i in self._transmitted if i not in self._placements][
                :max(0, len(self._transmitted) - self.MAX_TRANSMITTED)]:
            del self._transmitted[image_id]
            after.append(Pictures.free(image_id))
            trace(f"TUIScreen: freed picture {image_id} in the terminal, {self.MAX_TRANSMITTED} newer ones are kept")
        self._shown = {image_id: placement for image_id, (_, placement) in self._placements.items()}
        return before, after

    def get_frame(self, use_sync: bool = True) -> bytes:
        """Generate diff output between the back and front buffers."""
        changes: List[str] = []
        images_before, images_after = self._image_commands()
        has_diff = bool(self.control_sequences or images_before or images_after)
        CSI = self.CSI
        next_frame = self.next_frame
        front_buffer = self.front_buffer
        height = self.height
        width = self.width

        for row_idx in range(height):
            next_row = next_frame[row_idx]
            front_row = front_buffer[row_idx]
            col = 0
            while col < width:
                nc = next_row[col]
                oc = front_row[col]

                # Inline cell comparison to avoid function call overhead
                ns = nc.style
                os_ = oc.style
                if nc.char == oc.char and (
                    ns is os_
                    or (ns.bold == os_.bold and ns.italic == os_.italic and ns.underline == os_.underline
                        and ns.href == os_.href
                        and (ns.fg is os_.fg or (ns.fg is not None and os_.fg is not None
                             and ns.fg.r == os_.fg.r and ns.fg.g == os_.fg.g and ns.fg.b == os_.fg.b))
                        and (ns.bg is os_.bg or (ns.bg is not None and os_.bg is not None
                             and ns.bg.r == os_.bg.r and ns.bg.g == os_.bg.g and ns.bg.b == os_.bg.b)))
                ):
                    col += 1
                    continue

                has_diff = True
                run_style = ns
                run_chars = [nc.char]
                end_col = col + 1

                while end_col < width:
                    cn = next_row[end_col]
                    co = front_row[end_col]
                    cs = cn.style
                    cos = co.style
                    # Check cn != co (cell differs)
                    cell_same = cn.char == co.char and (
                        cs is cos
                        or (cs.bold == cos.bold and cs.italic == cos.italic and cs.underline == cos.underline
                            and cs.href == cos.href
                            and (cs.fg is cos.fg or (cs.fg is not None and cos.fg is not None
                                 and cs.fg.r == cos.fg.r and cs.fg.g == cos.fg.g and cs.fg.b == cos.fg.b))
                            and (cs.bg is cos.bg or (cs.bg is not None and cos.bg is not None
                                 and cs.bg.r == cos.bg.r and cs.bg.g == cos.bg.g and cs.bg.b == cos.bg.b)))
                    )
                    if cell_same:
                        break
                    # Check cn.style == run_style
                    rs = run_style
                    style_match = cs is rs or (
                        cs.bold == rs.bold and cs.italic == rs.italic and cs.underline == rs.underline
                        and cs.href == rs.href
                        and (cs.fg is rs.fg or (cs.fg is not None and rs.fg is not None
                             and cs.fg.r == rs.fg.r and cs.fg.g == rs.fg.g and cs.fg.b == rs.fg.b))
                        and (cs.bg is rs.bg or (cs.bg is not None and rs.bg is not None
                             and cs.bg.r == rs.bg.r and cs.bg.g == rs.bg.g and cs.bg.b == rs.bg.b))
                    )
                    if style_match:
                        run_chars.append(cn.char)
                        end_col += 1
                    else:
                        break

                changes.append(f"{CSI}{row_idx + 1};{col + 1}H")
                changes.extend(self._style_to_sequences(run_style))
                if run_style.href:
                    changes.append(self.link_open(run_style.href))
                    changes.append(''.join(run_chars))
                    changes.append(self.LINK_CLOSE)
                else:
                    changes.append(''.join(run_chars))

                for update_col in range(col, end_col):
                    fc = front_row[update_col]
                    nc2 = next_row[update_col]
                    fc.char = nc2.char
                    fc.style.copy_from(nc2.style)

                col = end_col

        if not has_diff:
            return b''

        output: List[str] = []
        if use_sync:
            output.append(self.BEGIN_SYNC)

        if self.control_sequences:
            output.extend(self.control_sequences)
            self.control_sequences = []

        output.extend(images_before)
        output.extend(changes)
        output.extend(images_after)
        output.append(self.RESET)

        if use_sync:
            output.append(self.END_SYNC)

        return ''.join(output).encode('utf-8')

    # Cursor control
    def move_cursor(self, x: int, y: int):
        """Move cursor to position (1-indexed)."""
        self.cursor_x = max(1, min(self.width, x))
        self.cursor_y = max(1, min(self.height, y))

    # Colors
    def set_color_rgb(self, r: float, g: float, b: float):
        self.current_style.fg = Color(
            self._clamp_color_component(r),
            self._clamp_color_component(g),
            self._clamp_color_component(b),
        )

    def set_bg_color_rgb(self, r: float, g: float, b: float):
        self.current_style.bg = Color(
            self._clamp_color_component(r),
            self._clamp_color_component(g),
            self._clamp_color_component(b),
        )

    def set_color(self, color: Color):
        self.current_style.fg = color

    def set_bg_color(self, color: Color):
        self.current_style.bg = color

    def reset_color(self):
        self.current_style = Style()

    # Text attributes
    def bold(self):
        self.current_style.bold = True

    def underline(self):
        self.current_style.underline = True

    def italic(self):
        self.current_style.italic = True

    def link(self, url: Optional[str]):
        """Make what is written next a clickable hyperlink to `url` (None ends it)."""
        self.current_style.href = url or None

    @classmethod
    def link_open(cls, url: str) -> str:
        """The OSC 8 opener for `url`. The id is derived from the url, so a link wrapped over
        several rows (or split into several diff runs) is one link to the terminal, which
        highlights all of it on hover. Everything outside printable ASCII is percent-encoded
        (OSC 8 carries bytes 32–126 only; `;` is encoded too since it separates the parts),
        the same URL once decoded; control characters alone are dropped."""
        printable = "".join(ch for ch in url if ord(ch) >= 0x20 and ch != "\x7f")
        safe = quote(printable, safe="/:?#[]@!$&'()*+,=%~")
        return f"{cls.OSC}8;id={zlib.crc32(safe.encode()):x};{safe}{cls.ST}"

    # Drawing helpers
    def draw_box(self, x: int, y: int, width: int, height: int,
                 style: str = "single", color: Optional[Color] = None):
        """Draw a box at specified position."""
        styles = {
            "single": {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"},
            "double": {"tl": "╔", "tr": "╗", "bl": "╚", "br": "╝", "h": "═", "v": "║"},
            "rounded": {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│"},
        }

        chars = styles.get(style, styles["single"])

        if color:
            self.set_color(color)

        if width < 2 or height < 2:
            return

        # Top line
        self.write_at(x, y, chars["tl"] + chars["h"] * (width - 2) + chars["tr"])

        # Vertical lines
        for i in range(1, height - 1):
            self.write_at(x, y + i, chars["v"])
            self.write_at(x + width - 1, y + i, chars["v"])

        # Bottom line
        self.write_at(x, y + height - 1,
                      chars["bl"] + chars["h"] * (width - 2) + chars["br"])

        if color:
            self.reset_color()

    def tint_row(self, x: int, y: int, width: int, bg: Color):
        """Give the cells of a row a background where they have none yet — a line tint
        under text already written, leaving cells with their own background alone."""
        if not 1 <= y <= self.height:
            return
        row = self.next_frame[y - 1]
        for cx in range(max(1, x), min(self.width, x + width - 1) + 1):
            style = row[cx - 1].style
            if style.bg is None:
                style.bg = bg

    def recolor_row(self, x: int, y: int, width: int, fg: Color, bold: bool = False):
        """Give the cells of a row a foreground (and bold, when asked), keeping the rest of
        their style — a marker over text already written."""
        if not 1 <= y <= self.height:
            return
        row = self.next_frame[y - 1]
        for cx in range(max(1, x), min(self.width, x + width - 1) + 1):
            style = row[cx - 1].style
            style.fg = fg
            style.bold = style.bold or bold

    def row_text(self, x: int, y: int, width: int) -> str:
        """The characters written to a row from column `x`, `width` cells wide — the text as
        it reads, so a wide glyph counts once (its continuation cell holds "")."""
        if not 1 <= y <= self.height:
            return ""
        row = self.next_frame[y - 1]
        return "".join(cell.char for cell in row[max(1, x) - 1:min(self.width, x + width - 1)])

    def fill_rect(self, x: int, y: int, width: int, height: int,
                  char: str = " ", bg_color: Optional[Color] = None):
        """Fill a rectangle with a character."""
        original_style = self.current_style.copy()

        if bg_color:
            self.set_bg_color(bg_color)

        for row in range(height):
            if y + row > self.height:
                break
            self.write_at(x, y + row, char * max(0, min(width, self.width - x + 1)))

        self.current_style = original_style

    def draw_progress_bar(self, x: int, y: int, width: int, progress: float,
                          filled_color: Optional[Color] = None,
                          empty_color: Optional[Color] = None,
                          marker: Optional[float] = None,
                          marker_color: Optional[Color] = None):
        """Single-value bar, optionally with a `marker` — a second value (0..1) drawn
        as a contrasting notch on top of the bar, for level-vs-watermark displays."""
        progress = max(0.0, min(1.0, progress))
        filled_chars = progress * width
        full_chars = int(filled_chars)
        partial_char = filled_chars - full_chars

        marker_idx = None
        if marker is not None and width > 0:
            marker_idx = max(0, min(width - 1, int(max(0.0, min(1.0, marker)) * width)))

        blocks = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]
        bar_chars: List[str] = []

        for _ in range(full_chars):
            bar_chars.append("█")

        if full_chars < width and partial_char > 0:
            block_index = int(partial_char * 8) + 1
            bar_chars.append(blocks[min(block_index, 8)])
            full_chars += 1

        for _ in range(full_chars, width):
            bar_chars.append(" ")

        self.move_cursor(x, y)

        original_style = self.current_style.copy()

        if empty_color:
            self.set_bg_color(empty_color)

        if filled_color or empty_color:
            for idx, char in enumerate(bar_chars):
                if idx == marker_idx:
                    self.set_color(marker_color or Color(235, 235, 235))
                    self.write("▌")
                    self.reset_color()
                    if empty_color:
                        self.set_bg_color(empty_color)
                    continue
                is_filled = (idx < int(filled_chars)) or (idx == int(filled_chars) and char != " ")

                if is_filled and filled_color:
                    self.set_color(filled_color)

                self.write(char)

                if filled_color and is_filled:
                    self.reset_color()
                    if empty_color:
                        self.set_bg_color(empty_color)
        else:
            if marker_idx is not None:
                bar_chars[marker_idx] = "▌"
            self.write("".join(bar_chars))

        self.set_bg_color(Color(0, 0, 0))
        if filled_color:
            self.set_color(filled_color)
        self.write("▏")

        self.current_style = original_style

    def _style_to_sequences(self, style: Style) -> List[str]:
        """Translate a style into escape sequences. A style without a background gets the
        BACKGROUND, never the terminal's default."""
        if style.is_default():
            return [self.RESET, self.BACKGROUND_SEQ]

        sequences = [self.RESET]

        if style.bold:
            sequences.append(self.BOLD)
        if style.italic:
            sequences.append(self.ITALIC)
        if style.underline:
            sequences.append(self.UNDERLINE)
        if style.fg:
            sequences.append(str(style.fg))
        sequences.append(style.bg.bg() if style.bg else self.BACKGROUND_SEQ)

        return sequences
