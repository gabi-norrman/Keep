"""Draws an RGB pixel surface onto the cell screen two pixels per cell, the top one as the
foreground of a `▀` half-block and the bottom one as its background.

The surface is a floats-0..1 array of shape (2 × rows, cols, 3). Each channel is quantized to
`LEVELS` steps on the way to the terminal: identical colors share one `Color` object (the
screen diff's identity fast path), and a pixel whose shade drifts a hair between frames is
not re-sent. The coarser the steps the longer the runs and the fewer the cells that change,
at the price of banding in the gradients. The Crystal renders through here and so does a
`Picture` on a terminal without a graphics protocol."""
from typing import Dict, Tuple

import numpy as np

from interfaces.console.framework.TUIScreen import TUIScreen, Color

LEVELS = 64
_PALETTE: Dict[Tuple[int, int], Color] = {}
UPPER_HALF = "▀"


def pack(r, g, b, levels: int = LEVELS):
    """One int per palette entry, from a triple of quantization levels (scalars or arrays)."""
    return (r * levels + g) * levels + b


def color(packed: int, levels: int = LEVELS) -> Color:
    """The Color for a packed triple of quantization levels."""
    key = (levels, packed)
    found = _PALETTE.get(key)
    if found is None:
        channels = (packed // (levels * levels), packed // levels % levels, packed % levels)
        found = _PALETTE[key] = Color(*(round(channel * 255 / (levels - 1)) for channel in channels))
    return found


def terminal_color(r: int, g: int, b: int, levels: int = LEVELS) -> Color:
    """The one Color object a pixel of this color is blitted with."""
    return color(pack(*(int(c / 255 * (levels - 1) + 0.5) for c in (r, g, b)), levels=levels), levels)


def blit(screen: TUIScreen, x: int, y: int, surface: np.ndarray, levels: int = LEVELS):
    """Draw `surface` with its top-left pixel at cell (x, y). Neighbouring cells showing the
    same pixel pair go out as one run, and a cell whose two pixels match is a plain space
    with a background, which is half the escape bytes of a half-block. A screen without
    `half_blocks` (Terminal.app draws `▀` with gaps and the wrong colors) gets every pixel
    pair averaged into one plain cell: half the vertical resolution, but the picture holds
    together."""
    clipped = np.clip(surface, 0, 1)
    height, width = clipped.shape[0], clipped.shape[1]
    rows = height // 2
    if not screen.half_blocks:
        clipped = clipped[:2 * rows].reshape(rows, 2, width, 3).mean(axis=1).repeat(2, axis=0)
    quantized = (clipped * (levels - 1) + 0.5).astype(np.int32)
    packed = pack(quantized[..., 0], quantized[..., 1], quantized[..., 2], levels)
    for row in range(rows):
        top = packed[2 * row].tolist()
        bottom = packed[2 * row + 1].tolist()
        col = 0
        while col < width:
            t, b = top[col], bottom[col]
            end = col + 1
            while end < width and top[end] == t and bottom[end] == b:
                end += 1
            screen.reset_color()
            screen.set_bg_color(color(b, levels))
            if t == b:
                screen.write_at(x + col, y + row, " " * (end - col))
            else:
                screen.set_color(color(t, levels))
                screen.write_at(x + col, y + row, UPPER_HALF * (end - col))
            col = end
    screen.reset_color()
