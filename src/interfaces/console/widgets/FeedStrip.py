"""The feed status strip at the right of the Home tab's footer: one chip per running feed,
"Feeds  ● iMessage · ▃ Photos ETA: 13h · ● Indigo". A live feed is a green dot, one still starting
a dim dot, a stopped or failed one a red cross; a catch-up is a one-cell progress bar filling
bottom up — the done part in green, the rest a darker green box behind it — with the ETA after
the name, the same figure the Dashboard shows, or, before the rate is known, what is left. The
colours are faded a quarter toward black so the strip sits quietly under the transcript. A
paused feed hollows its dot or fades its bar half toward black. Feeds that are not running (a
disabled integration) are simply absent. The summary is rebuilt at most once a second: the pane
redraws every frame and each feed's status takes its runner's lock."""
import time
from typing import List, NamedTuple, Optional

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.framework.TextWidth import display_width

HEADER = "Feeds  "
SEPARATOR = " · "
SHORT_NAMES = {"iCloud Photos": "Photos"}

BAR_BLOCKS = " ▁▂▃▄▅▆▇█"    # the catch-up bar's fill in eighths, bottom up; index 0 is the empty box
SYMBOL_RUNNING = "●"
SYMBOL_PAUSED = "○"
SYMBOL_FAILED = "✕"

COLOR_STARTING = Theme.COLOR_DIM   # ChatPane.COLOR_DIM: the strip's own text colour
SYMBOL_DIMMING = 0.75                   # the symbol's colour, faded a quarter of the way to black
PAUSED_DIMMING = 0.5                    # a paused bar, faded half way
BAR_EMPTY_DIMMING = 0.3                 # the box behind the fill: the same green, most of the way to black


def dimmed(color: Color, factor: float = SYMBOL_DIMMING) -> Color:
    return Color(int(color.r * factor), int(color.g * factor), int(color.b * factor))


def bar_block(fraction: float) -> str:
    """The lower block that fills `fraction` of a cell, rounded down to eighths so only 1.0 is full."""
    return BAR_BLOCKS[int(max(0.0, min(1.0, fraction)) * (len(BAR_BLOCKS) - 1))]


def format_eta(seconds: float) -> str:
    """One rounded unit: "45s", "13m", "13h", "3d"."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


class FeedChip(NamedTuple):
    symbol: str
    color: Color
    label: str                          # "Photos ETA: 13h", drawn in the footer's dim colour after the symbol
    background: Optional[Color] = None  # the box behind a catch-up bar; None for the dots and the cross

    @property
    def width(self) -> int:
        return display_width(self.symbol) + 1 + display_width(self.label)


def chip_for(name: str, status: dict) -> FeedChip:
    state = status["state"]
    label = SHORT_NAMES.get(name, name)
    if state in ("stopped", "failed"):
        return FeedChip(SYMBOL_FAILED, dimmed(Theme.COLOR_RED), label)
    if state == "catching_up":
        if status.get("eta_seconds") is not None:
            label += f" ETA: {format_eta(status['eta_seconds'])}"
        elif status.get("remaining") is not None:
            label += f" {status['remaining']} left"
        fill, box = dimmed(Theme.COLOR_GREEN), dimmed(Theme.COLOR_GREEN, BAR_EMPTY_DIMMING)
        if status.get("paused"):
            fill, box = dimmed(fill, PAUSED_DIMMING), dimmed(box, PAUSED_DIMMING)
        return FeedChip(bar_block(status.get("progress") or 0.0), fill, label, box)
    color = Theme.COLOR_GREEN if state == "live" else COLOR_STARTING
    return FeedChip(SYMBOL_PAUSED if status.get("paused") else SYMBOL_RUNNING, dimmed(color), label)


def strip_width(chips: List[FeedChip]) -> int:
    """The header, the chips and the separators between them; 0 without chips."""
    if not chips:
        return 0
    return display_width(HEADER) + sum(c.width for c in chips) + display_width(SEPARATOR) * (len(chips) - 1)


class FeedStrip:
    REFRESH_SECONDS = 1.0

    def __init__(self):
        self._chips: List[FeedChip] = []
        self._built_at: Optional[float] = None

    def chips(self, now: Optional[float] = None) -> List[FeedChip]:
        now = time.monotonic() if now is None else now
        if self._built_at is None or now - self._built_at >= self.REFRESH_SECONDS:
            self._chips = self._build()
            self._built_at = now
        return self._chips

    @staticmethod
    def _build() -> List[FeedChip]:
        from assistant.integration.FeedRunner import Feeds
        feeds = Feeds.get_instance()
        chips = []
        for name in feeds.names():
            status = feeds.status(name)
            if status is not None:
                chips.append(chip_for(name, status))
        return chips
