"""The Dashboard's chart plots drawn as pixels, for a terminal that shows pictures: columns
and stacked bars on the panel ground, each shaded from its colour's deep companion up to the
colour, with rounded anti-aliased tops, dashed rules and a soft glow around the newest column.

A plot is `cols` × `rows` cells at `Pictures.CELL_PX` pixels a cell, the size the screen sends
a picture at, so the PNG goes out unscaled. The value scale runs from the centre of the bottom
cell row to the centre of the top one — the rows a tile's y-axis labels stand on — which also
leaves half a row of air above full scale for a rounded top and its glow. A bar is given in
cells (`Bar.x0`/`x1`) and its segments as fractions of full scale; everything else is pixels.

`ChartPicture` is one chart's `Picture` kept across frames: it draws again only when what the
chart shows has changed, at most every `REFRESH_SECONDS` (unless the box changed, which
draws at once), and replaces the picture's image in place, so the terminal holds one image
per chart however long the tab is up."""
import time
from typing import Any, Callable, Dict, NamedTuple, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter

from common.logging import trace
from interfaces.console import Theme
from interfaces.console.framework.Pictures import CELL_PX, Picture
from interfaces.console.framework.TUIScreen import Color

BAR_PAD_PX = 2          # air either side of a column inside its cells
CAP_RADIUS_PX = 5       # the rounding of a bar's top corners
CAP_SAMPLES = 4         # sub-samples per pixel side the rounded top is anti-aliased with
DASH_PX = 3             # a dashed rule's dash, and the gap after it
SEPARATOR_PX = 2        # the panel ground showing between stacked segments
GLOW_RADIUS_PX = 7
GLOW_STRENGTH = 0.5
BASE_MIX = 0.8          # a colour's bottom shade: its deep companion this far out of the panel ground

Rule = Tuple[float, Color]    # (fraction of full scale, colour) of a dashed line across the plot


class Segment(NamedTuple):
    start: float        # fractions of full scale, bottom and top
    end: float
    color: Color


class Bar(NamedTuple):
    x0: int             # the cells it stands on, x1 exclusive
    x1: int
    segments: Sequence[Segment]     # bottom to top


def columns(values: Sequence[float], colors: Sequence[Color], max_value: float, cols: int, rows: int,
            rules: Sequence[Rule] = (), shade_min_rows: int = 0, glow: bool = True) -> Image.Image:
    """One column a cell for each value, the newest at the right and the plot's left empty
    while there are fewer values than cells. A column shades up to its colour at the height
    of the tallest column on show, never faster than the whole range over `shade_min_rows`
    rows, so a quiet chart keeps its colour."""
    values, colors = list(values)[-cols:], list(colors)[-cols:]
    offset = cols - len(values)
    scale = max_value if max_value > 0 else 1.0
    bars = [Bar(offset + i, offset + i + 1, [Segment(0.0, value / scale, color)])
            for i, (value, color) in enumerate(zip(values, colors)) if value > 0]
    peak = max((bar.segments[-1].end for bar in bars), default=0.0)
    span = max(peak, shade_min_rows / max(1, rows - 1))
    newest = bars[-1] if glow and bars and bars[-1].x1 == cols else None
    return render(bars, cols, rows, rules, shade_top=peak, shade_span=span, glow_bar=newest)


def render(bars: Sequence[Bar], cols: int, rows: int, rules: Sequence[Rule] = (),
           shade_top: float = 1.0, shade_span: float = 1.0, glow_bar: Optional[Bar] = None) -> Image.Image:
    """The plot as an RGB image of `cols` × `rows` cells. A colour shades by height: its base
    at `shade_top - shade_span` of full scale and below, the colour itself at `shade_top`."""
    cell_w, cell_h = CELL_PX
    width, height = cols * cell_w, rows * cell_h
    inset = cell_h / 2 if rows > 1 else 0.0
    zero_y = height - inset
    plot_h = max(1.0, height - 2 * inset)
    canvas = np.empty((height, width, 3), np.float32)
    canvas[:] = _rgb(Theme.COLOR_PANEL_BG)

    for fraction, color in rules:
        y = int(min(height - 1, max(0, np.floor(zero_y - fraction * plot_h))))
        dashes = (np.arange(width) // DASH_PX) % 2 == 0
        canvas[y, dashes] = _rgb(color)

    # The value fraction at each pixel row's centre, top row first.
    row_values = (zero_y - (np.arange(height) + 0.5)) / plot_h
    shades: Dict[Color, np.ndarray] = {}

    def shade(color: Color) -> np.ndarray:
        if color not in shades:
            base = _rgb(Theme.mix(Theme.COLOR_PANEL_BG, Theme.deep(color), BASE_MIX))
            t = np.clip(1 - (shade_top - row_values) / max(shade_span, 1e-6), 0, 1)[:, None]
            shades[color] = base + (_rgb(color) - base) * t
        return shades[color]

    for bar in bars:
        _draw_bar(canvas, bar, zero_y, plot_h, shade)
    if glow_bar is not None:
        _glow(canvas, glow_bar, zero_y, plot_h)
    return Image.fromarray(np.clip(canvas + 0.5, 0, 255).astype(np.uint8))


def _draw_bar(canvas: np.ndarray, bar: Bar, zero_y: float, plot_h: float, shade: Callable[[Color], np.ndarray]):
    height, width = canvas.shape[:2]
    x0 = bar.x0 * CELL_PX[0] + BAR_PAD_PX
    x1 = min(width, bar.x1 * CELL_PX[0] - BAR_PAD_PX)
    segments = [s for s in bar.segments if s.end > s.start]
    if x1 <= x0 or not segments:
        return
    bottom = zero_y - segments[0].start * plot_h
    top = max(0.0, min(zero_y - segments[-1].end * plot_h, bottom - 1))   # anything above zero shows a pixel
    y0, y1 = int(top), min(height, int(np.ceil(bottom)))
    if y1 <= y0:
        return

    # Each pixel row takes the colour of the segment its centre is in, at that row's shade.
    centres = np.arange(y0, y1) + 0.5
    ends = np.array([zero_y - s.end * plot_h for s in segments])
    index = np.clip(np.searchsorted(-ends, -centres, side="left"), 0, len(segments) - 1)
    colors = np.empty((y1 - y0, 3), np.float32)
    for i, segment in enumerate(segments):
        rows = index == i
        colors[rows] = shade(segment.color)[y0:y1][rows]

    alpha = np.ones((y1 - y0, x1 - x0), np.float32)
    for boundary in ends[:-1]:
        lo = max(y0, int(round(boundary - SEPARATOR_PX / 2)))
        alpha[lo - y0:max(lo, int(round(boundary + SEPARATOR_PX / 2))) - y0] = 0
    radius = min(CAP_RADIUS_PX, (x1 - x0) / 2, bottom - top)
    cap = _cap(x1 - x0, radius, top - y0)
    rows = min(len(cap), len(alpha))
    alpha[:rows] *= cap[:rows]

    region = canvas[y0:y1, x0:x1]
    a = alpha[..., None]
    region[:] = region * (1 - a) + colors[:, None, :] * a


_caps: Dict[Tuple[int, float, float], np.ndarray] = {}


def _cap(width: int, radius: float, offset: float) -> np.ndarray:
    """The coverage (0..1) of the top pixel rows of a `width`-pixel bar whose top edge lies
    `offset` (0..1) into its first row and whose top corners are rounded by `radius`."""
    key = (width, round(radius, 2), round(offset * 8) / 8)
    found = _caps.get(key)
    if found is None:
        width, radius, offset = key
        rows = max(1, int(np.ceil(offset + radius)))
        step = 1 / CAP_SAMPLES
        sub = (np.arange(CAP_SAMPLES) + 0.5) * step
        sy = (np.arange(rows)[:, None] + sub[None, :]).reshape(-1)[:, None]
        sx = (np.arange(width)[:, None] + sub[None, :]).reshape(-1)[None, :]
        centre_x = np.clip(sx, radius, width - radius)
        corner = (sy < offset + radius) & ((sx - centre_x) ** 2 + (sy - offset - radius) ** 2 > radius ** 2)
        inside = (sy >= offset) & ~corner
        found = _caps[key] = inside.reshape(rows, CAP_SAMPLES, width, CAP_SAMPLES).mean(axis=(1, 3)).astype(np.float32)
    return found


def _glow(canvas: np.ndarray, bar: Bar, zero_y: float, plot_h: float):
    """A soft halo of the bar's top colour on the ground around it, never over the bar."""
    height, width = canvas.shape[:2]
    margin = 3 * GLOW_RADIUS_PX
    x0 = bar.x0 * CELL_PX[0] + BAR_PAD_PX
    x1 = min(width, bar.x1 * CELL_PX[0] - BAR_PAD_PX)
    top = int(max(0, zero_y - bar.segments[-1].end * plot_h))
    bottom = int(min(height, np.ceil(zero_y - bar.segments[0].start * plot_h)))
    if x1 <= x0 or bottom <= top:
        return
    cx0, cx1 = max(0, x0 - margin), min(width, x1 + margin)
    mask = np.zeros((height, cx1 - cx0), np.uint8)
    mask[top:bottom, x0 - cx0:x1 - cx0] = 255
    blurred = np.asarray(Image.fromarray(mask).filter(ImageFilter.GaussianBlur(GLOW_RADIUS_PX)), np.float32) / 255
    glow = (blurred * (mask == 0) * GLOW_STRENGTH)[..., None]
    region = canvas[:, cx0:cx1]
    region[:] = region + (_rgb(bar.segments[-1].color) - region) * glow


def _rgb(color: Color) -> np.ndarray:
    return np.array((color.r, color.g, color.b), np.float32)


class ChartPicture:
    """One chart's picture across frames, drawn again only when its `signature` — any value
    that compares equal while the chart shows the same — changes."""

    REFRESH_SECONDS = 0.5

    def __init__(self, name: str):
        self.name = name
        self.picture: Optional[Picture] = None
        self._signature: Any = None
        self._box: Optional[Tuple[int, int]] = None
        self._drawn_at = 0.0

    def update(self, cols: int, rows: int, signature: Any, draw: Callable[[], Image.Image]) -> Picture:
        """The picture for a `cols` × `rows` box showing what `signature` stands for, from
        `draw()` when the one held is stale and due for a refresh."""
        now = time.monotonic()
        box = (cols, rows)
        resized = box != self._box
        due = signature != self._signature and now - self._drawn_at >= self.REFRESH_SECONDS
        if self.picture is None or resized or due:
            image = draw()
            if self.picture is None:
                self.picture = Picture(image)
                trace(f"ChartPicture {self.name}: picture {self.picture.id} for a {cols}x{rows} box")
            else:
                self.picture.replace(image)
                if resized:
                    trace(f"ChartPicture {self.name}: redrawn for a {cols}x{rows} box")
            self._signature, self._box, self._drawn_at = signature, box, now
        return self.picture

