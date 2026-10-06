"""Heuristics to detect when an Arlo snapshot is a cached CDN thumbnail.

Arlo's cloud sometimes returns a previously-cached, downscaled thumbnail
instead of a freshly-captured frame. pyaarlo accepts these as if they were
fresh because the underlying ignore_date=True path skips timestamp checks.

Empirically these stale thumbnails differ from real captures in two ways:
  * resolution: cached thumbnails are sub-1280 wide (e.g. 640x357, 720x720),
    while fresh Arlo captures are 1920x1024 native.
  * encoder fingerprint: stale thumbnails carry an FFmpeg "Lavc..." JPEG
    comment from the CDN re-encode; fresh captures have a plain JFIF header
    and no comment.

EXIF DateTimeOriginal would be the cleanest signal, but Arlo (at least the
cameras in this deployment) does not embed EXIF — both fresh and stale
JPEGs come back with empty EXIF tables — so the heuristics above are what
we have to work with.
"""

from io import BytesIO
from typing import Tuple

from PIL import Image

MIN_FRESH_WIDTH = 1280
LAVC_MARKER = b"Lavc"


def looks_like_cached_thumbnail(jpeg_bytes: bytes) -> Tuple[bool, str]:
    """Return (is_thumbnail, reason). reason is a short string for logging."""
    if not jpeg_bytes:
        return False, ""

    try:
        img = Image.open(BytesIO(jpeg_bytes))
        img.load()
    except Exception as e:
        return False, f"unparseable ({e})"

    width, height = img.size
    comment = img.info.get("comment") or b""
    if isinstance(comment, str):
        comment = comment.encode("utf-8", errors="ignore")

    reasons = []
    if width < MIN_FRESH_WIDTH:
        reasons.append(f"{width}x{height}")
    if LAVC_MARKER in comment:
        marker = comment.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        reasons.append(marker)

    if reasons:
        return True, ", ".join(reasons)
    return False, ""
