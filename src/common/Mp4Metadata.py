"""Minimal MP4 (ISO BMFF) box reader — enough to learn how a clip should be displayed."""
import struct
from typing import Optional


def mp4_display_size(data: bytes) -> Optional[tuple[int, int]]:
    """The (width, height) a player shows *data* at, or None when no video track header is found.

    Phone cameras often encode a portrait clip as a landscape stream plus a 90° rotation matrix
    in the track header, so the coded size alone gives the wrong aspect ratio. Telegram lays a
    video out from the coded size unless told otherwise, hence this helper."""
    for offset, size in _boxes(data, 0, len(data), b"moov"):
        for trak_offset, trak_size in _boxes(data, offset, offset + size, b"trak"):
            for tkhd_offset, tkhd_size in _boxes(data, trak_offset, trak_offset + trak_size, b"tkhd"):
                dims = _tkhd_display_size(data[tkhd_offset:tkhd_offset + tkhd_size])
                if dims:
                    return dims
    return None


def mp4_duration_seconds(data: bytes) -> Optional[int]:
    """Whole-second length of the presentation from the movie header, or None if absent."""
    seconds = mp4_duration(data)
    return round(seconds) if seconds is not None else None


def mp4_duration(data: bytes) -> Optional[float]:
    """Length of the presentation in seconds from the movie header. None when there is no
    header, or when it says zero (fragmented / live-recorded clips leave it unset)."""
    for offset, size in _boxes(data, 0, len(data), b"moov"):
        for mvhd_offset, mvhd_size in _boxes(data, offset, offset + size, b"mvhd"):
            payload = data[mvhd_offset:mvhd_offset + mvhd_size]
            if not payload:
                continue
            if payload[0] == 1:
                if len(payload) < 32:
                    continue
                timescale, duration = struct.unpack(">IQ", payload[20:32])
            else:
                if len(payload) < 20:
                    continue
                timescale, duration = struct.unpack(">II", payload[12:20])
            if timescale and duration:
                return duration / timescale
    return None


def _boxes(data: bytes, start: int, end: int, wanted: bytes):
    """Yield (payload offset, payload size) of every *wanted* box directly inside [start, end)."""
    end = min(end, len(data))  # a box may claim more than the (possibly truncated) file holds
    pos = start
    while pos + 8 <= end:
        size, box_type = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        if box_type == wanted:
            yield pos + header, size - header
        pos += size


def _tkhd_display_size(payload: bytes) -> Optional[tuple[int, int]]:
    if not payload:
        return None
    version = payload[0]
    # version+flags, creation/modification time, track id, reserved, duration
    pos = 4 + (32 if version == 1 else 20)
    pos += 8 + 2 + 2 + 2 + 2  # reserved, layer, alternate group, volume, reserved
    if len(payload) < pos + 36 + 8:
        return None
    a, b, _, c, d = struct.unpack(">5i", payload[pos:pos + 20])  # matrix rows: (a b u) (c d v) (x y w)
    width, height = struct.unpack(">II", payload[pos + 36:pos + 44])
    width, height = round(width / 65536), round(height / 65536)
    if not width or not height:
        return None  # audio / metadata tracks carry zero size
    rotated = abs(a) < 0x8000 and abs(d) < 0x8000 and abs(b) >= 0x8000 and abs(c) >= 0x8000
    return (height, width) if rotated else (width, height)
