"""Bakes a clip's display rotation into its pixels, for players that read the coded frames and drop the matrix."""
import os
import tempfile
import threading
import time
from typing import Optional

from common.logging import error, info, trace

EXPORT_TIMEOUT_SECONDS = 180
_STATUS_COMPLETED = 3  # AVAssetExportSessionStatusCompleted


def bake_rotation(video_bytes: bytes, timeout: float = EXPORT_TIMEOUT_SECONDS) -> Optional[bytes]:
    """*video_bytes* re-encoded so that the frames are stored the way the track's display matrix
    shows them and the matrix itself becomes identity. None when it cannot be done — AVFoundation
    missing (not a Mac), no video track, or an export that failed or timed out — so the caller
    sends the clip as it is.

    A phone's portrait clip is a landscape stream plus a 90° matrix; Telegram for iOS reads the
    matrix for the layout, then renders the unrotated raster into it. Only a clip whose matrix is
    not identity needs this, see `Mp4Metadata.mp4_is_rotated`."""
    try:
        import AVFoundation as AV
        from CoreMedia import CMTimeMake, CMTimeRangeMake, kCMTimeZero
        from Foundation import NSURL
        from Quartz import (CGAffineTransformConcat, CGAffineTransformMakeTranslation, CGRectApplyAffineTransform,
                            CGRectMake)
    except ImportError as e:
        info(f"VideoRotation: AVFoundation not available ({e}), clip sent as it is")
        return None

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="floxy-rotate-") as folder:
        source = os.path.join(folder, "source.mp4")
        target = os.path.join(folder, "upright.mp4")
        with open(source, "wb") as f:
            f.write(video_bytes)

        asset = AV.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(source), None)
        tracks = asset.tracksWithMediaType_(AV.AVMediaTypeVideo)
        if not tracks:
            info("VideoRotation: no video track in the clip, sent as it is")
            return None
        track = tracks[0]
        natural = track.naturalSize()
        transform = track.preferredTransform()
        # The matrix maps the coded frame onto the display; the frame lands wherever its
        # translation says, so shift it back to the origin before rendering into a canvas of
        # the displayed size.
        shown = CGRectApplyAffineTransform(CGRectMake(0, 0, natural.width, natural.height), transform)
        transform = CGAffineTransformConcat(transform, CGAffineTransformMakeTranslation(-shown.origin.x, -shown.origin.y))
        width, height = round(abs(shown.size.width)), round(abs(shown.size.height))

        layer = AV.AVMutableVideoCompositionLayerInstruction.videoCompositionLayerInstructionWithAssetTrack_(track)
        layer.setTransform_atTime_(transform, kCMTimeZero)
        instruction = AV.AVMutableVideoCompositionInstruction.videoCompositionInstruction()
        instruction.setTimeRange_(CMTimeRangeMake(kCMTimeZero, asset.duration()))
        instruction.setLayerInstructions_([layer])
        composition = AV.AVMutableVideoComposition.videoComposition()
        composition.setRenderSize_((width, height))
        frame_rate = track.nominalFrameRate() or 30
        composition.setFrameDuration_(CMTimeMake(1000, round(frame_rate * 1000)))
        composition.setInstructions_([instruction])

        # The 720p preset fits either orientation (a 720x1280 canvas stays 720x1280) and caps the
        # bitrate, so a rendition near the chat's upload limit does not grow past it.
        export = AV.AVAssetExportSession.exportSessionWithAsset_presetName_(asset, AV.AVAssetExportPreset1280x720)
        if export is None:
            error("VideoRotation: AVFoundation refused to create an export session for the clip")
            return None
        export.setOutputURL_(NSURL.fileURLWithPath_(target))
        export.setOutputFileType_(AV.AVFileTypeMPEG4)
        export.setVideoComposition_(composition)
        export.setShouldOptimizeForNetworkUse_(True)
        done = threading.Event()
        export.exportAsynchronouslyWithCompletionHandler_(done.set)
        if not done.wait(timeout):
            export.cancelExport()
            error(f"VideoRotation: export did not finish within {timeout:.0f}s, clip sent as it is")
            return None
        if export.status() != _STATUS_COMPLETED:
            err = export.error()
            error(f"VideoRotation: export failed (status {export.status()}, "
                  f"{err.localizedDescription() if err else 'no error given'}), clip sent as it is")
            return None
        with open(target, "rb") as f:
            upright = f.read()
    trace(f"VideoRotation: {round(natural.width)}x{round(natural.height)} frames re-encoded as {width}x{height} "
          f"in {time.monotonic() - started:.1f}s ({len(video_bytes) / 1024 / 1024:.1f} -> {len(upright) / 1024 / 1024:.1f} MB)")
    return upright
