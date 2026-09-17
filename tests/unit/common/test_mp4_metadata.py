import struct

from common.Mp4Metadata import mp4_display_size, mp4_duration, mp4_duration_seconds

IDENTITY = (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
ROTATE_90 = (0, 0x10000, 0, -0x10000, 0, 0, 720 << 16, 0, 0x40000000)


def box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def tkhd(width: int, height: int, matrix=IDENTITY, version=0) -> bytes:
    head = bytes([version, 0, 0, 0]) + bytes(32 if version == 1 else 20) + bytes(16)
    return box(b"tkhd", head + struct.pack(">9i", *matrix) + struct.pack(">II", width << 16, height << 16))


def mvhd(timescale: int, duration: int, version=0) -> bytes:
    if version == 1:
        payload = bytes([1, 0, 0, 0]) + bytes(16) + struct.pack(">IQ", timescale, duration)
    else:
        payload = bytes(4) + bytes(8) + struct.pack(">II", timescale, duration)
    return box(b"mvhd", payload + bytes(80))


def movie(*tracks: bytes, header: bytes = mvhd(600, 34_560)) -> bytes:
    return box(b"ftyp", b"isom") + box(b"moov", header + b"".join(box(b"trak", t) for t in tracks))


def test_landscape_clip():
    assert mp4_display_size(movie(tkhd(1280, 720))) == (1280, 720)


def test_rotation_matrix_swaps_dimensions():
    assert mp4_display_size(movie(tkhd(1280, 720, ROTATE_90))) == (720, 1280)


def test_skips_audio_track_and_reads_version_1_header():
    assert mp4_display_size(movie(tkhd(0, 0), tkhd(720, 1280, version=1))) == (720, 1280)


def test_moov_after_mdat():
    data = box(b"mdat", b"\x00" * 50) + movie(tkhd(640, 480))
    assert mp4_display_size(data) == (640, 480)


def test_no_video_track():
    assert mp4_display_size(movie(tkhd(0, 0))) is None
    assert mp4_display_size(b"not an mp4 at all") is None
    assert mp4_display_size(b"") is None


def test_duration_from_movie_header():
    assert mp4_duration_seconds(movie(tkhd(1280, 720))) == 58  # 34560 / 600 = 57.6
    assert mp4_duration_seconds(movie(header=mvhd(1000, 62_067, version=1))) == 62


def test_duration_missing():
    assert mp4_duration_seconds(movie(header=mvhd(0, 100))) is None
    assert mp4_duration_seconds(b"not an mp4 at all") is None


def test_duration_zero_means_unknown():
    assert mp4_duration(movie(header=mvhd(600, 0))) is None  # fragmented / live clips leave it unset
    assert mp4_duration_seconds(movie(header=mvhd(600, 0))) is None


def test_fractional_duration():
    assert mp4_duration(movie(header=mvhd(600, 240))) == 0.4
    assert mp4_duration_seconds(movie(header=mvhd(600, 240))) == 0


def test_box_claiming_more_than_the_file_holds():
    """A nested box whose declared size overruns its parent, or a truncated download, must not
    raise: the Telegram send that reads the header has no other chance to go out."""
    overrun = box(b"moov", struct.pack(">I4s", 0xFFFFFFF0, b"trak") + b"\x00" * 4)
    assert mp4_display_size(overrun) is None
    assert mp4_duration_seconds(overrun) is None
    complete = movie(tkhd(1280, 720))
    for cut in range(1, len(complete)):
        mp4_display_size(complete[:cut])
        mp4_duration_seconds(complete[:cut])


def box64(box_type: bytes, payload: bytes) -> bytes:
    """A box with the 64-bit "largesize" header (size field 1), as large mdat atoms use."""
    return struct.pack(">I4sQ", 1, box_type, 16 + len(payload)) + payload


def box_to_eof(box_type: bytes, payload: bytes) -> bytes:
    """A box whose size field is 0: it runs to the end of the file (streamed / progressive files)."""
    return struct.pack(">I4s", 0, box_type) + payload


def test_largesize_and_to_eof_boxes():
    tracks = mvhd(600, 1200) + box(b"trak", tkhd(640, 480))
    large = box(b"ftyp", b"isom") + box64(b"mdat", b"\x00" * 30) + box64(b"moov", tracks)
    assert mp4_display_size(large) == (640, 480) and mp4_duration(large) == 2.0
    open_ended = box(b"ftyp", b"isom") + box_to_eof(b"moov", tracks)
    assert mp4_display_size(open_ended) == (640, 480) and mp4_duration(open_ended) == 2.0
    # Both must survive truncation anywhere, like the 32-bit layout does.
    for data in (large, open_ended):
        for cut in range(1, len(data)):
            mp4_display_size(data[:cut])
            mp4_duration_seconds(data[:cut])
