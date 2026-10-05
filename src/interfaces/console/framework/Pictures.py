"""A picture on the cell screen: a decoded image fitted to a box of cells, drawn through the
kitty graphics protocol on a terminal that has it and as half-block pixels on one that has not.

`Picture.decode` turns JPEG or PNG bytes into a `Picture` (RGB, EXIF-oriented); `fit` gives the
cell box that keeps its proportions, taking a cell as half as wide as it is tall; `png` is the
picture resized for that box as the protocol's payload and `pixels` the same box as a half-block
surface for `Pixels.blit`. Both renderings are cached per box, so a frame that shows the same
picture again costs nothing. The module functions build the protocol's APC sequences
(`ESC _ G … ESC \\`): `transmit` sends the PNG in chunks under an image id, `place` shows a
transmitted image over a run of cells at the cursor — a crop of it when only some of its rows
are on screen — and `delete_placements` / `free` / `delete_all` take images down. Every
command carries `q=2`, so the terminal never answers on the keyboard channel; a terminal
without the protocol ignores the sequences. Ghostty, iTerm2 (3.6+), WezTerm and kitty itself
speak it; `TUIServer.GRAPHICS_TERMINALS` names them."""
import base64
import io
import itertools
import threading
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageOps

from common.logging import info, trace

CELL_ASPECT = 0.5          # a cell's width over its height: what a picture's proportions are fitted with
CELL_PX = (16, 32)         # the pixels a cell is given when a picture is sized for the protocol (a Retina cell, roughly)
CHUNK = 4096               # base64 characters per transmit chunk, the protocol's maximum
RESAMPLE = Image.LANCZOS
PNG_COMPRESS_LEVEL = 3     # fast to encode on the TUI thread; a photo gains little from a slower level

APC = "\x1b_G"
ST = "\x1b\\"
Box = Tuple[int, int]


class Picture:
    """One decoded image and its renderings for the cell boxes it has been drawn in. `id` is
    the protocol's image id, unique for the process."""

    _ids = itertools.count(1)
    _lock = threading.Lock()

    def __init__(self, image: Image.Image, size_bytes: int = 0):
        with Picture._lock:
            self.id = next(Picture._ids)
        self.image = image
        self.size_bytes = size_bytes
        self._png: Dict[Box, Tuple[bytes, Tuple[int, int]]] = {}
        self._pixels: Dict[Box, np.ndarray] = {}

    @classmethod
    def decode(cls, data: Optional[bytes]) -> Optional["Picture"]:
        """A Picture from image bytes, or None (logged) when they are not an image PIL reads."""
        if not data:
            trace("Picture: no bytes to decode")
            return None
        try:
            image = Image.open(io.BytesIO(data))
            image = ImageOps.exif_transpose(image) or image
            image = image.convert("RGB")
        except Exception as e:
            info(f"Picture: {len(data)} bytes could not be decoded as an image: {e}")
            return None
        picture = cls(image, len(data))
        trace(f"Picture {picture.id}: {image.width}x{image.height} from {len(data)} bytes")
        return picture

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    def fit(self, max_cols: int, max_rows: int) -> Box:
        """The largest cell box within `max_cols` × `max_rows` with the picture's proportions,
        at least one cell each way."""
        aspect = self.width / max(1, self.height)
        rows = max(1, max_rows)
        cols = round(rows * aspect / CELL_ASPECT)
        if cols > max_cols:
            cols = max(1, max_cols)
            rows = round(cols * CELL_ASPECT / aspect)
        return max(1, cols), max(1, rows)

    def png(self, cols: int, rows: int) -> Tuple[bytes, Tuple[int, int]]:
        """(PNG bytes, pixel size) of the picture resized for a `cols` × `rows` box: `CELL_PX`
        per cell, never larger than the source."""
        box = (cols, rows)
        cached = self._png.get(box)
        if cached is None:
            size = (min(self.width, cols * CELL_PX[0]), min(self.height, rows * CELL_PX[1]))
            resized = self.image if size == self.image.size else self.image.resize(size, RESAMPLE)
            out = io.BytesIO()
            resized.save(out, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
            cached = self._png[box] = (out.getvalue(), resized.size)
            trace(f"Picture {self.id}: {resized.width}x{resized.height} PNG of {len(cached[0]) // 1024} kB "
                  f"for a {cols}x{rows} box")
        return cached

    def pixels(self, cols: int, rows: int) -> np.ndarray:
        """The picture as a (2 × rows, cols, 3) float surface for `Pixels.blit`."""
        box = (cols, rows)
        cached = self._pixels.get(box)
        if cached is None:
            resized = self.image.resize((cols, 2 * rows), RESAMPLE)
            cached = self._pixels[box] = np.asarray(resized, np.float32) / 255
        return cached


def transmit(image_id: int, png: bytes) -> str:
    """The chunks that hand `png` to the terminal under `image_id`, replacing any image it
    holds under that id."""
    data = base64.b64encode(png).decode("ascii")
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [""]
    out = []
    for i, chunk in enumerate(chunks):
        more = 1 if i < len(chunks) - 1 else 0
        keys = f"a=t,t=d,f=100,i={image_id},q=2,m={more}" if i == 0 else f"m={more}"
        out.append(f"{APC}{keys};{chunk}{ST}")
    return "".join(out)


def place(image_id: int, cols: int, rows: int, png_size: Tuple[int, int],
          first_row: int = 0, visible_rows: Optional[int] = None) -> str:
    """Show image `image_id` over `cols` × `rows` cells at the cursor, which stays put. With
    `first_row` and `visible_rows` only that band of the rows is shown — the matching
    band of the source — over `visible_rows` cells, for a picture half scrolled away."""
    visible = rows if visible_rows is None else visible_rows
    keys = f"a=p,i={image_id},c={cols},r={visible},C=1,q=2"
    if first_row or visible != rows:
        width, height = png_size
        top = round(height * first_row / rows)
        bottom = round(height * (first_row + visible) / rows)
        keys += f",x=0,y={top},w={width},h={max(1, bottom - top)}"
    return f"{APC}{keys}{ST}"


def delete_placements(image_id: int) -> str:
    """Take image `image_id` off the screen, keeping its data for a later `place`."""
    return f"{APC}a=d,d=i,i={image_id},q=2{ST}"


def free(image_id: int) -> str:
    """Take image `image_id` off the screen and drop its data."""
    return f"{APC}a=d,d=I,i={image_id},q=2{ST}"


def delete_all() -> str:
    """Take every image off the screen and drop all of their data."""
    return f"{APC}a=d,d=A,q=2{ST}"
