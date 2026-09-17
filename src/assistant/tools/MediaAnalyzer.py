import os
from typing import Optional

from common.logging import info, trace
from .ImageAnalyzer import image_analyzer
from .VideoAnalyzer import video_analyzer

# The one place that knows which attachments Floxy can describe. Integrations filter on these.
IMAGE_MIME_PREFIXES = ("image/jpeg", "image/png", "image/heic", "image/heif", "image/tiff", "image/webp", "image/gif")
VIDEO_MIME_PREFIXES = ("video/mp4", "video/quicktime", "video/mpeg", "video/webm", "video/3gpp", "video/x-m4v")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".gif")
VIDEO_EXTENSION_MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg", ".webm": "video/webm", ".3gp": "video/3gpp",
}
VIDEO_EXTENSIONS = tuple(VIDEO_EXTENSION_MIME)

# ISO base media (ftyp) brands that are still images — HEIC/AVIF share the container with mp4/mov.
_ISOBMFF_IMAGE_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis")

KIND_IMAGE = "image"
KIND_VIDEO = "video"

DEFAULT_FALLBACK = {KIND_IMAGE: "[Photo]", KIND_VIDEO: "[Video]"}


def is_image_mime(mime_type: Optional[str]) -> bool:
    return bool(mime_type) and any(mime_type.startswith(p) for p in IMAGE_MIME_PREFIXES)


def is_video_mime(mime_type: Optional[str]) -> bool:
    return bool(mime_type) and any(mime_type.startswith(p) for p in VIDEO_MIME_PREFIXES)


def media_kind(mime_type: Optional[str] = None, filename: Optional[str] = None,
               raw_bytes: Optional[bytes] = None) -> Optional[str]:
    """"image", "video" or None. Trusts the MIME type first, then the file extension, then the
    bytes' magic signature — whichever the caller can provide."""
    if is_image_mime(mime_type):
        return KIND_IMAGE
    if is_video_mime(mime_type):
        return KIND_VIDEO

    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            return KIND_IMAGE
        if ext in VIDEO_EXTENSIONS:
            return KIND_VIDEO

    if raw_bytes:
        return _kind_from_magic(raw_bytes)
    return None


def _kind_from_magic(raw: bytes) -> Optional[str]:
    head = raw[:16]
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*", b"BM")):
        return KIND_IMAGE
    if head[4:8] == b"ftyp":
        return KIND_IMAGE if head[8:12] in _ISOBMFF_IMAGE_BRANDS else KIND_VIDEO
    if head.startswith(b"RIFF"):
        if head[8:12] == b"WEBP":
            return KIND_IMAGE
        if head[8:12] == b"AVI ":
            return KIND_VIDEO
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # EBML: webm / mkv
        return KIND_VIDEO
    return None


class MediaAnalyzer:
    """Routes a media attachment to ImageAnalyzer or VideoAnalyzer by its kind.

    Use `media_analyzer()` for the process-wide instance; the constructor arguments exist so tests
    can inject fakes.
    """

    def __init__(self, image=None, video=None):
        self._image = image
        self._video = video

    def video_size_limit(self) -> int:
        """Bytes above which describe_bytes() skips a video; check it before downloading one."""
        return (self._video or video_analyzer()).max_size()

    def describe_bytes(self, raw_bytes: bytes, mime_type: Optional[str] = None, filename: Optional[str] = None,
                       context: str = "", fallback: Optional[str] = None) -> str:
        """Describe an image or video. *fallback* defaults to "[Photo]" / "[Video]" by kind;
        pass one explicitly to use the same placeholder for both."""
        kind = media_kind(mime_type, filename, raw_bytes)
        if kind is None:
            info(f"MediaAnalyzer: unknown media kind (mime={mime_type!r}, filename={filename!r}, "
                 f"{len(raw_bytes)} bytes), leaving it undescribed")
            return fallback if fallback is not None else "[Attachment]"

        fallback = fallback if fallback is not None else DEFAULT_FALLBACK[kind]
        trace(f"MediaAnalyzer: {kind} (mime={mime_type!r}, filename={filename!r}) → {kind} analyzer")
        if kind == KIND_VIDEO:
            return (self._video or video_analyzer()).describe_bytes(
                raw_bytes, mime_type=video_mime_type(mime_type, filename), context=context, fallback=fallback)
        return (self._image or image_analyzer()).describe_bytes(raw_bytes, context=context, fallback=fallback)


def video_mime_type(mime_type: Optional[str], filename: Optional[str]) -> Optional[str]:
    """The MIME type to send with a video: the reported one if it is a real video type, else one
    derived from the extension, else None (VideoAnalyzer then assumes mp4)."""
    if is_video_mime(mime_type):
        return mime_type
    if filename:
        return VIDEO_EXTENSION_MIME.get(os.path.splitext(filename)[1].lower())
    return None


_default = MediaAnalyzer()


def media_analyzer() -> MediaAnalyzer:
    return _default
