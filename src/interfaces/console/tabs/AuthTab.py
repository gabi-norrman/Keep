"""The Integrations tabs that sign a cloud account in: iCloud, Arlo, Ring and Telegram.

They share one board in the Dashboard's tiles: summary tiles (the connection and whatever the
tab counts — devices, cameras), the tab's own table (its device list) and a Sign-in tile with
the integration's message, the code entry or a hint on how to start it, and the outcome of the
last action. From 150 columns the Sign-in tile sits beside the table, above it on a narrower
screen. The status check runs on a background thread every CHECK_INTERVAL seconds and when the
tab is opened. A subclass says how to reach its integration, what its table and tiles hold and
how to verify a code; the key handling for a six-digit code is here too.
"""
import threading
import time
from typing import Callable, List, NamedTuple, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import fit
from interfaces.console.tabs.ScrollableTabView import COLOR_HINT, ScrollableTabView, draw_scrollbar
from interfaces.console.tabs.TabView import TabView, SUB_CONTENT_TOP
from interfaces.console.widgets import Tile
from common.logging import info, trace

COLOR_GREEN = Theme.COLOR_GREEN
COLOR_YELLOW = Theme.COLOR_YELLOW
COLOR_RED = Theme.COLOR_RED
COLOR_GRAY = Theme.COLOR_LABEL
COLOR_WHITE = Theme.COLOR_BRIGHT
COLOR_PROMPT = Theme.COLOR_YELLOW   # the field an entry waits on

# The names TUIFloxyConsole._parse_key gives these keys, plus the raw bytes some paths pass
ENTER_KEYS = ('\r', '\n', 'enter')
BACKSPACE_KEYS = ('\x7f', '\x08', 'backspace')

# Words in an action's outcome that colour it green (done) or yellow (still going); any
# other outcome reads as a failure, in red.
DONE_WORDS = ("success", "refreshed", "verified", "auto-detected", "connected")
BUSY_WORDS = ("verifying", "connecting", "waiting", "sending", "signing")

CODE_LENGTH = 6
STANDARD_ENTRY_HINT = "Enter submit  Esc cancel"
PENDING = "–"

LEFT = 3
RIGHT_MARGIN = 2
GAP = 2
SUMMARY_H = 1 + Tile.CARD_CHROME
SUMMARY_TILE_MIN_W = 30     # a narrower screen shows the first tiles only
SIDE_BY_SIDE_MIN = 150      # screen columns from which the Sign-in tile sits beside the table
SIDE_MIN_W = 50
FORM_MAX_W = 100            # a tab without a table: its Sign-in tile stays readable on a wide screen
FORM_LABEL_W = 13
MESSAGE_ROWS = 2            # the integration's message wraps to this many rows

# A summary tile: its label, the value in its title strip, the value's colour, the row under it.
SummaryTile = Tuple[str, str, Color, List[Tuple[str, Color]]]
# A row of the Sign-in tile: its label, its value, the value's colour, whether the entry waits on it.
Field = Tuple[str, str, Color, bool]


class Table(NamedTuple):
    """A tab's own list: the tile's label, the columns as (title, width) with the last width
    None for what is left, the rows as (dot colour, a (text, colour) per column), and what to
    say while there are none."""
    title: str
    columns: List[Tuple[str, Optional[int]]]
    rows: List[Tuple[Color, List[Tuple[str, Color]]]]
    empty: str


def code_display(code: str, length: int) -> str:
    """The digits typed so far, spaced out, with a slot for each one still to come."""
    shown = " ".join(code) if code else "_"
    return shown + " _" * (length - len(code))


class AuthTab(TabView):
    HELP = "Ctrl-R refresh  Ctrl-E enter 2FA code"
    CHECK_INTERVAL = 30.0
    SUBMITTING_MESSAGE = "Submitting 2FA code..."
    # The Connection tile's value per auth status; anything else reads as not initialized
    STATUS_LABELS = {
        "authenticated": ("Connected", COLOR_GREEN),
        "error": ("Error", COLOR_RED),
    }

    def __init__(self):
        self._api = None
        self._auth_status: dict = {"status": "not_initialized", "message": "Not checked yet"}
        self._status_message = ""      # the outcome of the last action
        self._input_mode = False       # truthy while the tab is taking typed input
        self._input_text = ""
        self._last_check_time = 0.0
        self._checking = False
        self._cards: List[Tile.Card] = []
        self._top = 0

    # --- what a subclass provides ------------------------------------------------------------

    def _connect(self):
        """The integration singleton; raises when it cannot be built (not configured)."""
        raise NotImplementedError

    def _on_status(self, api) -> None:
        """After a status check: fetch whatever the tab's section shows (device names)."""

    def _table(self) -> Optional[Table]:
        """The tab's own list (its devices); None for a tab that has only the Sign-in tile."""
        return None

    def _tiles(self) -> List[SummaryTile]:
        """The summary tiles after the Connection one."""
        return []

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        """What to say in place of the entry prompt while no input is being taken."""
        return None

    def _verify_code(self, api, code: str) -> None:
        """Submit `code` to the integration on the worker thread and set the outcome."""
        raise NotImplementedError

    def _refresh(self) -> None:
        """Ctrl+R."""
        raise NotImplementedError

    # --- status ------------------------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._auth_status.get("status", "unknown")

    def _get_api(self):
        if self._api is not None and self._rebuilt(self._api):
            trace(f"{self.name} tab: the integration was rebuilt — dropping the cached instance")
            self._api = None
        if self._api is None:
            try:
                self._api = self._connect()
            except Exception as e:
                # Asked every frame by some tabs: log a failure once, not thirty times a second
                if self._auth_status.get("message") != str(e):
                    trace(f"{self.name} tab: integration unavailable: {e}")
                self._auth_status = {"status": "error", "message": str(e)}
        return self._api

    @staticmethod
    def _rebuilt(api) -> bool:
        """True when *api* is a singleton the monitor has since replaced (a restart after
        config_request): a tab built before the section existed must not keep refreshing,
        or send a 2FA code to, the unconfigured instance."""
        peek = getattr(type(api), "peek_instance", None)
        return peek is not None and peek() is not api

    def _check_auth_status(self):
        api = self._get_api()
        if api:
            self._auth_status = api.get_auth_status()
            self._on_status(api)
        self._last_check_time = time.time()

    def _check_auth_status_async(self):
        """Run _check_auth_status on a background thread, one at a time."""
        if self._checking:
            return
        self._checking = True

        def check():
            try:
                self._check_auth_status()
            finally:
                self._checking = False

        self._run(check)

    @staticmethod
    def _run(fn: Callable[[], None]):
        threading.Thread(target=fn, daemon=True).start()

    # --- drawing -----------------------------------------------------------------------------

    def _before_draw(self):
        """Per-frame hook ahead of drawing (the iCloud and Ring tabs pick up the auto-2FA outcome)."""

    def draw(self, screen: TUIScreen, frame: int):
        if time.time() - self._last_check_time > self.CHECK_INTERVAL:
            self._check_auth_status_async()
        self._before_draw()

        self._cards = []
        available = screen.width - LEFT - RIGHT_MARGIN
        bottom = screen.height - 1                # the last row is the key hints'
        y = SUB_CONTENT_TOP
        table = self._table()
        fields = self._fields()
        form_h = Tile.CARD_CHROME + MESSAGE_ROWS + len(fields) + 2   # a blank row, then the outcome

        if bottom - y - SUMMARY_H - 1 >= form_h:
            self._draw_summary(screen, y, available, [self._connection_tile()] + self._tiles())
            y += SUMMARY_H + 1
        height = bottom - y

        if table is None:
            self._draw_form(screen, LEFT, y, min(available, FORM_MAX_W), min(form_h, height), fields)
        elif screen.width >= SIDE_BY_SIDE_MIN:
            side_w = max(SIDE_MIN_W, available * 36 // 100)
            table_w = available - GAP - side_w
            self._draw_table(screen, LEFT, y, table_w, height, table)
            self._draw_form(screen, LEFT + table_w + GAP, y, side_w, min(form_h, height), fields)
        else:
            self._draw_form(screen, LEFT, y, available, min(form_h, height), fields)
            y += form_h + 1
            self._draw_table(screen, LEFT, y, available,
                             min(max(1, len(table.rows)) + Tile.CARD_CHROME + 1, bottom - y), table)
        Tile.tint_cards(screen, self._cards)

        screen.reset_color()
        screen.set_color(COLOR_HINT)
        screen.write_at(LEFT, screen.height - 1, fit(self._hints(), max(0, available)))
        screen.reset_color()

    def _hints(self) -> str:
        """The key hints on the bottom row: the entry's keys while one is open."""
        return STANDARD_ENTRY_HINT if self._input_mode else self.HELP

    # --- summary tiles -----------------------------------------------------------------------

    def _draw_summary(self, screen: TUIScreen, y: int, available: int, tiles: List[SummaryTile]):
        tiles = tiles[:max(1, available // SUMMARY_TILE_MIN_W)]
        count = len(tiles)
        width = (available - GAP * (count - 1)) // count
        for i, (label, value, color, segments) in enumerate(tiles):
            x = LEFT + i * (width + GAP)
            tile_w = width if i < count - 1 else available - (width + GAP) * (count - 1)
            Tile.draw_card(screen, self._cards, x, y, tile_w, SUMMARY_H, label, value, color)
            Tile.write_segments(screen, x + 2, y + Tile.CARD_TOP, tile_w - 4, segments)

    def _status_label(self) -> Tuple[str, Color]:
        if self._checking:
            return "Checking...", COLOR_YELLOW
        return self.STATUS_LABELS.get(self.status, ("Not initialized", Theme.COLOR_DIM))

    def _connection_tile(self) -> SummaryTile:
        label, color = self._status_label()
        if not self._last_check_time:
            return "Connection", label, color, [("not checked yet", Theme.COLOR_DIM)]
        age = max(0, int(time.time() - self._last_check_time))
        return ("Connection", label, color,
                [(f"checked {age}s ago", Theme.COLOR_LABEL), (f"every {self.CHECK_INTERVAL:.0f}s", Theme.COLOR_DIM)])

    # --- the table ---------------------------------------------------------------------------

    def _draw_table(self, screen: TUIScreen, x: int, y: int, width: int, height: int, table: Table):
        """The tab's list, one row each under a state dot; ↑↓ scroll a list longer than its tile."""
        if height <= Tile.CARD_CHROME:
            return
        count = len(table.rows)
        Tile.draw_card(screen, self._cards, x, y, width, height, table.title, str(count) if count else "")
        inner_w = width - 4
        header_y = y + Tile.CARD_TOP
        if not table.rows:
            screen.set_color(Theme.COLOR_DIM)
            screen.write_at(x + 2, header_y, fit(table.empty, max(0, inner_w)))
            screen.reset_color()
            return
        room = height - Tile.CARD_CHROME - 1    # a row goes to the column titles
        if room <= 0:
            return
        self._top = max(0, min(self._top, count - room))
        widths = [column_w for _, column_w in table.columns]
        Tile.write_cells(screen, x + 4, header_y, inner_w - 2,
                         [(title, column_w, Theme.COLOR_DIM, False) for title, column_w in table.columns])
        for i, (dot, cells) in enumerate(table.rows[self._top:self._top + room]):
            row_y = header_y + 1 + i
            screen.reset_color()
            screen.set_color(dot)
            screen.write_at(x + 2, row_y, "●")
            Tile.write_cells(screen, x + 4, row_y, inner_w - 2,
                             [(text, column_w, color, False) for (text, color), column_w in zip(cells, widths)])
        draw_scrollbar(screen, x + width - 1, header_y + 1, count, room, count - room - self._top)
        screen.reset_color()

    # --- the Sign-in tile --------------------------------------------------------------------

    def _fields(self) -> List[Field]:
        """The Sign-in tile's rows under the message: the code while it is typed, else the
        hint on how to start the entry."""
        if self._input_mode:
            return [("2FA code", code_display(self._input_text, CODE_LENGTH), Theme.COLOR_BRIGHT, True)]
        hint = self._entry_hint()
        if hint:
            return [("2FA code", hint[0], hint[1], False)]
        return [("2FA code", "not asked for", Theme.COLOR_FAINT, False)]

    def _message_color(self) -> Color:
        if self.status == "error":
            return COLOR_RED
        return Theme.COLOR_TEXT if self.status in self.STATUS_LABELS else Theme.COLOR_LABEL

    def _draw_form(self, screen: TUIScreen, x: int, y: int, width: int, height: int, fields: List[Field]):
        """What the integration says about its session, the entry's fields — the one it waits
        on with a yellow label — and on the last row the outcome of the last action."""
        if height <= Tile.CARD_CHROME:
            return
        waiting = any(field[3] for field in fields)
        Tile.draw_card(screen, self._cards, x, y, width, height, "Sign-in",
                       "waiting for you" if waiting else "", COLOR_PROMPT)
        value_x = x + 2 + FORM_LABEL_W
        value_w = max(1, width - 4 - FORM_LABEL_W)
        last = y + height - 2
        row_y = y + Tile.CARD_TOP

        message = self._auth_status.get("message", "") or PENDING
        lines = ScrollableTabView.word_wrap(message, value_w)
        if len(lines) > MESSAGE_ROWS:
            lines = lines[:MESSAGE_ROWS - 1] + [fit(" ".join(lines[MESSAGE_ROWS - 1:]), value_w)]
        screen.set_color(Theme.COLOR_DIM)
        screen.write_at(x + 2, row_y, "Status")
        screen.set_color(self._message_color())
        for i, line in enumerate(lines):
            if row_y + i <= last:
                screen.write_at(value_x, row_y + i, line)
        row_y += MESSAGE_ROWS

        for i, (name, text, text_color, is_waiting) in enumerate(fields):
            if row_y + i > last:
                break
            screen.reset_color()
            screen.set_color(COLOR_PROMPT if is_waiting else Theme.COLOR_DIM)
            screen.write_at(x + 2, row_y + i, name)
            screen.set_color(text_color)
            if is_waiting:
                screen.bold()
            screen.write_at(value_x, row_y + i, fit(text, value_w))
        screen.reset_color()

        outcome = self._outcome()
        if outcome and last >= row_y + len(fields):
            screen.set_color(self._outcome_color(outcome))
            screen.write_at(x + 2, last, fit(outcome, max(0, width - 4)))
            screen.reset_color()

    def _outcome(self) -> str:
        """The outcome line to show (the Arlo tab swaps in its rate-limit countdown)."""
        return self._status_message

    @staticmethod
    def _outcome_color(outcome: str) -> Color:
        lowered = outcome.lower()
        if any(w in lowered for w in DONE_WORDS):
            return COLOR_GREEN
        if any(w in lowered for w in BUSY_WORDS):
            return COLOR_YELLOW
        return COLOR_RED

    # --- keys --------------------------------------------------------------------------------

    def handle_input(self, key: str) -> bool:
        if self._input_mode:
            return self._handle_entry_key(key)
        if key == '\x12':  # Ctrl+R
            self._refresh()
            return True
        if key in ('up', 'down'):
            self._top = max(0, self._top + (1 if key == 'down' else -1))
            return True
        return self._handle_key(key)

    def _handle_key(self, key: str) -> bool:
        """The tab's own shortcuts outside entry mode; Ctrl+E starts the code entry."""
        if key == '\x05':
            self._begin_entry()
            return True
        return False

    def _begin_entry(self):
        self._input_mode = True
        self._input_text = ""

    def _cancel_entry(self):
        self._input_mode = False
        self._input_text = ""
        self._status_message = ""

    def _handle_entry_key(self, key: str) -> bool:
        """A six-digit code: digits fill it (submitted when complete), Enter submits a complete
        one, Backspace edits, Esc cancels. Every key is consumed while entering."""
        if key == 'escape':
            self._cancel_entry()
        elif key in ENTER_KEYS:
            if len(self._input_text) == CODE_LENGTH:
                self._submit_code()
        elif key in BACKSPACE_KEYS:
            self._input_text = self._input_text[:-1]
        elif key.isdigit() and len(self._input_text) < CODE_LENGTH:
            self._input_text += key
            if len(self._input_text) == CODE_LENGTH:
                self._submit_code()
        return True

    def _submit_code(self):
        code = self._input_text
        self._act(self.SUBMITTING_MESSAGE, lambda api: self._verify_code(api, code))

    def _act(self, message: str, body: Callable[[object], None]):
        """Leave entry mode and run `body(api)` on a worker thread with `message` as the
        outcome meanwhile; an exception becomes the outcome."""
        api = self._get_api()
        if not api:
            self._status_message = "Error: API not initialized"
            self._input_mode = False
            return
        self._input_mode = False
        self._input_text = ""
        self._status_message = message

        def run():
            try:
                body(api)
            except Exception as e:
                self._status_message = f"Error: {e}"
                info(f"{self.name} tab: {message.rstrip('.')} failed: {e}")

        self._run(run)

    # --- tab lifecycle -----------------------------------------------------------------------

    def on_activate(self):
        self._check_auth_status_async()

    def on_deactivate(self):
        self._input_mode = False
        self._input_text = ""
