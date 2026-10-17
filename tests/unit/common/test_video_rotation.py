"""bake_rotation: a clip whose track matrix turns it comes back with the turn in the pixels."""
import sys
import threading
from unittest.mock import patch

import pytest

from common.Mp4Metadata import mp4_display_size, mp4_duration, mp4_is_rotated
from common.VideoRotation import bake_rotation

WIDTH, HEIGHT, FRAMES = 320, 180, 12
RED, BLUE = (0, 0, 255, 255), (255, 0, 0, 255)  # BGRA


def _portrait_clip(path) -> bytes:
    """An H.264 clip with WIDTHxHEIGHT frames (left half red, right half blue) and the 90° matrix
    an iPhone writes for a portrait recording: shown turned, red at the top."""
    import AVFoundation as AV
    import CoreMedia as CM
    import Quartz
    from Foundation import NSURL
    from Quartz import CoreVideo as CV

    writer = AV.AVAssetWriter.assetWriterWithURL_fileType_error_(NSURL.fileURLWithPath_(str(path)), AV.AVFileTypeMPEG4, None)[0]
    settings = {AV.AVVideoCodecKey: AV.AVVideoCodecTypeH264, AV.AVVideoWidthKey: WIDTH, AV.AVVideoHeightKey: HEIGHT}
    track = AV.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_(AV.AVMediaTypeVideo, settings)
    track.setTransform_(Quartz.CGAffineTransformMake(0, 1, -1, 0, HEIGHT, 0))
    attributes = {CV.kCVPixelBufferPixelFormatTypeKey: CV.kCVPixelFormatType_32BGRA,
                  CV.kCVPixelBufferWidthKey: WIDTH, CV.kCVPixelBufferHeightKey: HEIGHT}
    adaptor = AV.AVAssetWriterInputPixelBufferAdaptor.assetWriterInputPixelBufferAdaptorWithAssetWriterInput_sourcePixelBufferAttributes_(
        track, attributes)
    writer.addInput_(track)
    writer.startWriting()
    writer.startSessionAtSourceTime_(CM.kCMTimeZero)
    for i in range(FRAMES):
        while not track.isReadyForMoreMediaData():
            pass
        _, buffer = CV.CVPixelBufferCreate(None, WIDTH, HEIGHT, CV.kCVPixelFormatType_32BGRA, None, None)
        CV.CVPixelBufferLockBaseAddress(buffer, 0)
        stride = CV.CVPixelBufferGetBytesPerRow(buffer)
        row = bytearray(stride)
        for x in range(WIDTH):
            row[x * 4:x * 4 + 4] = RED if x < WIDTH // 2 else BLUE
        view = CV.CVPixelBufferGetBaseAddress(buffer).as_buffer(stride * HEIGHT)
        for y in range(HEIGHT):
            view[y * stride:(y + 1) * stride] = row
        CV.CVPixelBufferUnlockBaseAddress(buffer, 0)
        adaptor.appendPixelBuffer_withPresentationTime_(buffer, CM.CMTimeMake(i, 30))
    track.markAsFinished()
    done = threading.Event()
    writer.finishWritingWithCompletionHandler_(done.set)
    assert done.wait(10) and writer.status() == 2, writer.error()
    return path.read_bytes()


def _stored_frame(path):
    """The pixels of the middle frame as stored, ignoring the track matrix: (width, height, pixel(x, y) -> (r, g, b))."""
    import AVFoundation as AV
    import CoreMedia as CM
    import Quartz
    from Foundation import NSURL

    asset = AV.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    generator = AV.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
    generator.setAppliesPreferredTrackTransform_(False)
    image = generator.copyCGImageAtTime_actualTime_error_(CM.CMTimeMake(FRAMES // 2, 30), None, None)[0]
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    stride = Quartz.CGImageGetBytesPerRow(image)
    data = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image)))

    def pixel(x, y):
        b, g, r = data[y * stride + x * 4:y * stride + x * 4 + 3]  # the generator hands out BGRA
        return r > 128, g > 128, b > 128  # the codec blurs the colours a little

    return width, height, pixel


@pytest.fixture(scope="module")
def portrait(tmp_path_factory) -> bytes:
    pytest.importorskip("AVFoundation")
    return _portrait_clip(tmp_path_factory.mktemp("clip") / "portrait.mp4")


@pytest.mark.unit
class TestBakeRotation:
    def test_the_turn_moves_from_the_matrix_into_the_pixels(self, portrait, tmp_path):
        assert mp4_is_rotated(portrait) and mp4_display_size(portrait) == (HEIGHT, WIDTH)

        upright = bake_rotation(portrait)

        assert upright is not None and not mp4_is_rotated(upright)
        assert mp4_display_size(upright) == (HEIGHT, WIDTH)  # the same layout, now the coded size
        assert mp4_duration(upright) == pytest.approx(mp4_duration(portrait), abs=0.05)
        path = tmp_path / "upright.mp4"
        path.write_bytes(upright)
        width, height, pixel = _stored_frame(path)
        assert (width, height) == (HEIGHT, WIDTH)
        assert pixel(HEIGHT // 2, 10) == (True, False, False)  # the left (red) half turned to the top
        assert pixel(HEIGHT // 2, WIDTH - 10) == (False, False, True)

    def test_a_clip_without_a_video_track_is_left_alone(self):
        pytest.importorskip("AVFoundation")
        assert bake_rotation(b"not an mp4 at all") is None

    def test_without_avfoundation_nothing_is_done(self):
        with patch.dict(sys.modules, {"AVFoundation": None}):
            assert bake_rotation(b"\x00" * 64) is None
