"""Text that reveals itself character by character, with a CRT cursor sweeping along.

The one piece kept from the old Dialog tab. Feed it a stream (`append`/`set_text` as tokens
arrive) — the reveal runs at `cps` characters per second and speeds up in proportion to the
backlog, so a fast LLM stream never lags behind while a short reply still types out. With
`reveal=False` the text simply shows: the chat pane uses that for everything the assistant
does not emit itself (its replies and thinking reveal).

Time only advances while someone asks (`visible_text()`), so text appended after a long
quiet spell types out instead of appearing at once. Single-threaded like `CrtCursor`: the
writer may swap the text from another thread, but drawing happens on the TUI loop.
"""
import time
from typing import Callable, Optional, Tuple

from interfaces.console.framework.TUIScreen import Color, TUIScreen
from interfaces.console.widgets.CrtCursor import CrtCursor


class TypewriterText:
    DEFAULT_CPS = 100
    # A backlog beyond CATCHUP_BACKLOG characters adds CATCHUP_PER_CHAR cps per extra
    # character, so a fast LLM stream trails by a line or two at most while a short line
    # (a command notification) still types out at the base speed.
    CATCHUP_BACKLOG = 50
    CATCHUP_PER_CHAR = 6.0

    def __init__(self, text: str = "", cps: float = DEFAULT_CPS, reveal: bool = True,
                 clock: Callable[[], float] = time.time):
        self._text = text
        self._cps = cps
        self._clock = clock
        self._instant = not reveal   # show everything at once, appended text included
        self._revealed = 0.0 if reveal else float(len(text))
        self._last_tick = clock()
        self._cursor = CrtCursor()

    @property
    def text(self) -> str:
        return self._text

    def append(self, text: str):
        if not text:
            return
        self._advance()
        self._text += text

    def set_text(self, text: str):
        """Replace the content; what was already revealed stays revealed."""
        self._advance()
        self._text = text
        self._revealed = min(self._revealed, float(len(text)))

    def reveal_all(self):
        self._revealed = float(len(self._text))

    def visible_text(self) -> str:
        self._advance()
        return self._text[:int(self._revealed)]

    @property
    def animated(self) -> bool:
        """False for text shown all at once: it never reveals or carries a cursor, so the
        pane can skip its per-frame cursor work."""
        return not self._instant

    @property
    def done(self) -> bool:
        self._advance()
        return int(self._revealed) >= len(self._text)

    def _advance(self):
        now = self._clock()
        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        backlog = len(self._text) - self._revealed
        if backlog <= 0 or self._instant:
            self._revealed = float(len(self._text))
            return
        rate = self._cps + max(0.0, backlog - self.CATCHUP_BACKLOG) * self.CATCHUP_PER_CHAR
        self._revealed = min(float(len(self._text)), self._revealed + elapsed * rate)

    # --- the sweeping cursor ----------------------------------------------------------------

    def reset_cursor(self):
        """Forget the sweep's screen positions (trail and head): the text moved on screen."""
        self._cursor.reset()

    def draw_cursor(self, screen: TUIScreen, frame: int, at: Optional[Tuple[int, int]],
                    *, char_at: Optional[Callable[[int, int], str]] = None,
                    fg: Optional[Color] = None, row_start_x: Optional[int] = None):
        """Draw the cursor head at `at` (x, y) — the end of the last visible line — or, with
        `at` None, only let the existing trail keep fading. Call once per frame."""
        if at is None:
            self._cursor.hide()
        else:
            self._cursor.update(at[0], at[1], row_start_x=row_start_x)
        self._cursor.draw(screen, frame, blink=False, char_at=char_at, fg=fg)
