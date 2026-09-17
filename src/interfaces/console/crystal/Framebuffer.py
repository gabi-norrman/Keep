import math
from typing import Dict, NamedTuple, Optional, Sequence, Tuple

import numpy as np

from interfaces.console.framework.TUIScreen import TUIScreen, Color

Point = Tuple[float, float]
Vec = Tuple[float, float, float]
RGB = Tuple[int, int, int]

# Each channel defaults to this many levels on the way to the terminal: identical colors
# share one Color object (the screen diff's identity fast path), and a face whose lighting
# drifts a hair between frames is not re-sent. The coarser the steps the longer the runs and
# the fewer the cells that change, at the price of banding in the gradients.
LEVELS = 64
_PALETTE: Dict[Tuple[int, int], Color] = {}
UPPER_HALF = "▀"


def _pack(r: int, g: int, b: int, levels: int = LEVELS) -> int:
    """One int per palette entry, from a triple of quantization levels."""
    return (r * levels + g) * levels + b


def _color(packed: int, levels: int = LEVELS) -> Color:
    """The Color for a packed triple of quantization levels."""
    key = (levels, packed)
    color = _PALETTE.get(key)
    if color is None:
        channels = (packed // (levels * levels), packed // levels % levels, packed % levels)
        color = _PALETTE[key] = Color(*(round(channel * 255 / (levels - 1)) for channel in channels))
    return color


def terminal_color(r: int, g: int, b: int, levels: int = LEVELS) -> Color:
    """The one Color object a pixel of this color is blitted with."""
    return _color(_pack(*(int(c / 255 * (levels - 1) + 0.5) for c in (r, g, b)), levels=levels), levels)


def focal_length(width: int, height: int) -> float:
    """Perspective onto a `width` × `height` surface; half-block pixels are near enough square
    that no aspect correction is needed."""
    return min(width, height) * 0.85


class Fragment(NamedTuple):
    """The pixels one face covers and passed the depth test at, with their view-space positions."""
    ys: np.ndarray
    xs: np.ndarray
    positions: np.ndarray   # (n, 3)


class Framebuffer:
    """A width × height RGB surface (`color`, floats 0..1) with a depth buffer, height always
    twice the cell rows it is blitted into: each terminal cell shows two pixels stacked, the
    top one as the foreground of a `▀` and the bottom one as its background. Pixel (x, y)
    covers the unit square from (x, y) and is sampled at its centre; `rays` holds the
    view-space direction through each centre, the camera sitting at the origin looking
    down -z. `zoom` scales the focal length: above 1 a narrower field of view, the scene
    larger and flatter. `centre` is the principal point in pixels, where the view axis
    lands on the surface: the middle unless `look_at` moves it, and moving it pans the
    picture without turning the camera, so an object on the axis keeps its centred look
    wherever on the surface it is drawn."""

    def __init__(self, width: int, height: int, zoom: float = 1.0):
        self.width = width
        self.height = height
        self.zoom = zoom
        self.focal = focal_length(width, height) * zoom
        self.color = np.zeros((height, width, 3), np.float32)
        self.depth = np.full((height, width), np.inf, np.float32)
        self.centre: Point = (-1.0, -1.0)
        self.look_at(0.5, 0.5)

    def look_at(self, fx: float, fy: float):
        """Put the principal point `fx` of the way across and `fy` of the way down the
        surface (0.5, 0.5 is the middle); the rays are rebuilt only when it moves."""
        centre = (fx * self.width, fy * self.height)
        if centre == self.centre:
            return
        self.centre = centre
        xs = (np.arange(self.width, dtype=np.float32) + 0.5 - centre[0]) / self.focal
        ys = -(np.arange(self.height, dtype=np.float32) + 0.5 - centre[1]) / self.focal
        self.rays = np.dstack(np.broadcast_arrays(xs[None, :], ys[:, None], np.float32(-1.0)))

    def project(self, v: Vec) -> Point:
        """Where the view-space point `v` lands on the surface, in pixels."""
        depth = -v[2]
        return self.centre[0] + self.focal * v[0] / depth, self.centre[1] - self.focal * v[1] / depth

    def clear(self):
        self.color.fill(0)
        self.depth.fill(np.inf)

    def get(self, x: int, y: int) -> RGB:
        return tuple(int(c * 255 + 0.5) for c in self.color[y, x])

    def _inside(self, points: Sequence[Point]) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Row and column indices of the pixels whose centre lies in the convex polygon."""
        pts = [(float(x), float(y)) for x, y in points]
        area = sum(ax * by - bx * ay for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]))
        if abs(area) < 1e-9:
            return None
        x0 = max(0, math.floor(min(p[0] for p in pts)))
        x1 = min(self.width - 1, math.ceil(max(p[0] for p in pts)))
        y0 = max(0, math.floor(min(p[1] for p in pts)))
        y1 = min(self.height - 1, math.ceil(max(p[1] for p in pts)))
        if x1 < x0 or y1 < y0:
            return None
        cx = np.arange(x0, x1 + 1, dtype=np.float32)[None, :] + 0.5
        cy = np.arange(y0, y1 + 1, dtype=np.float32)[:, None] + 0.5
        sign = 1.0 if area > 0 else -1.0
        mask = np.ones((y1 - y0 + 1, x1 - x0 + 1), bool)
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            mask &= ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) * sign >= 0
        ys, xs = np.nonzero(mask)
        if not len(ys):
            return None
        return ys + y0, xs + x0

    def rasterize(self, points: Sequence[Point], normal: Vec, origin: Vec) -> Optional[Fragment]:
        """The pixels inside the convex screen polygon `points` that are nearer than what the
        depth buffer holds, given that the face lies in the view-space plane through `origin`
        with `normal`. Each kept pixel's ray is intersected with that plane — exact,
        perspective-correct positions with no interpolation — and its depth is written."""
        hit = self._inside(points)
        if hit is None:
            return None
        ys, xs = hit
        rays = self.rays[ys, xs]
        along = rays @ np.asarray(normal, np.float32)
        along[along == 0] = np.float32(1e-12)
        positions = rays * (np.dot(origin, normal) / along)[:, None]
        depth = -positions[:, 2]
        keep = (depth > 0) & (depth < self.depth[ys, xs])
        if not keep.any():
            return None
        ys, xs = ys[keep], xs[keep]
        self.depth[ys, xs] = depth[keep]
        return Fragment(ys, xs, positions[keep])

    def paint(self, fragment: Fragment, colors: np.ndarray):
        """Write per-pixel colors (n × 3, floats 0..1) to a fragment's pixels."""
        self.color[fragment.ys, fragment.xs] = np.clip(colors, 0, 1)

    def fill_polygon(self, points: Sequence[Point], color: RGB):
        """Flat fill of a convex polygon, no depth test: the 2-D primitive."""
        hit = self._inside(points)
        if hit is not None:
            self.color[hit] = np.asarray(color, np.float32) / 255

    def downsample_into(self, target: "Framebuffer"):
        """Box filter into a surface `factor` times smaller each way: the anti-aliasing pass."""
        factor = self.width // target.width
        assert (self.width, self.height) == (factor * target.width, factor * target.height)
        blocks = self.color.reshape(target.height, factor, target.width, factor, 3)
        target.color[:] = blocks.mean(axis=(1, 3))

    def blit(self, screen: TUIScreen, x: int, y: int, levels: int = LEVELS):
        """Draw the surface with its top-left pixel at cell (x, y). Neighbouring cells showing
        the same pixel pair go out as one run, and a cell whose two pixels match is a plain
        space with a background, which is half the escape bytes of a half-block. A screen
        without `half_blocks` (Terminal.app draws `▀` with gaps and the wrong colors) gets
        every pixel pair averaged into one plain cell: half the vertical resolution, but
        the picture holds together."""
        color = np.clip(self.color, 0, 1)
        if not screen.half_blocks:
            rows = self.height // 2
            color = color[:2 * rows].reshape(rows, 2, self.width, 3).mean(axis=1).repeat(2, axis=0)
        quantized = (color * (levels - 1) + 0.5).astype(np.int32)
        packed = _pack(quantized[..., 0], quantized[..., 1], quantized[..., 2], levels)
        width = self.width
        for row in range(self.height // 2):
            top = packed[2 * row].tolist()
            bottom = packed[2 * row + 1].tolist()
            col = 0
            while col < width:
                t, b = top[col], bottom[col]
                end = col + 1
                while end < width and top[end] == t and bottom[end] == b:
                    end += 1
                screen.reset_color()
                screen.set_bg_color(_color(b, levels))
                if t == b:
                    screen.write_at(x + col, y + row, " " * (end - col))
                else:
                    screen.set_color(_color(t, levels))
                    screen.write_at(x + col, y + row, UPPER_HALF * (end - col))
                col = end
        screen.reset_color()
