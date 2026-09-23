from unittest.mock import Mock

import pytest

from assistant.tools.MediaAnalyzer import MediaAnalyzer, media_kind


@pytest.mark.unit
class TestMediaKind:
    @pytest.mark.parametrize("mime,expected", [
        ("image/jpeg", "image"), ("image/heic", "image"), ("image/png", "image"),
        ("video/mp4", "video"), ("video/quicktime", "video"), ("video/webm", "video"),
        ("audio/mp4", None), ("application/pdf", None), ("", None), (None, None),
    ])
    def test_from_mime(self, mime, expected):
        assert media_kind(mime_type=mime) == expected

    @pytest.mark.parametrize("filename,expected", [
        ("IMG_0001.JPG", "image"), ("photo.heic", "image"), ("clip.MOV", "video"),
        ("clip.mp4", "video"), ("voice.m4a", None), ("noext", None),
    ])
    def test_from_extension(self, filename, expected):
        assert media_kind(filename=filename) == expected

    def test_mime_wins_over_extension(self):
        assert media_kind(mime_type="video/mp4", filename="x.jpg") == "video"

    def test_unknown_mime_falls_back_to_extension(self):
        assert media_kind(mime_type="application/octet-stream", filename="x.mov") == "video"

    @pytest.mark.parametrize("raw,expected", [
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image"),
        (b"\xff\xd8\xff\xe0" + b"\0" * 12, "image"),
        (b"\0\0\0\x18ftypheic" + b"\0" * 4, "image"),
        (b"\0\0\0\x18ftypavif" + b"\0" * 4, "image"),
        (b"\0\0\0\x18ftypmp42" + b"\0" * 4, "video"),
        (b"\0\0\0\x14ftypqt  " + b"\0" * 4, "video"),
        (b"RIFF\0\0\0\0WEBPVP8 ", "image"),
        (b"RIFF\0\0\0\0AVI LIST", "video"),
        (b"\x1a\x45\xdf\xa3" + b"\0" * 12, "video"),
        (b"%PDF-1.4" + b"\0" * 8, None),
        (b"", None),
    ])
    def test_from_magic_bytes(self, raw, expected):
        assert media_kind(raw_bytes=raw) == expected

@pytest.mark.unit
class TestDescribeBytes:
    def _analyzer(self):
        image, video = Mock(), Mock()
        image.describe_bytes.return_value = "Image description: [x] y"
        video.describe_bytes.return_value = "Video description: [x] y"
        return MediaAnalyzer(image=image, video=video), image, video

    def test_image_routed_to_image_analyzer_with_photo_fallback(self):
        analyzer, image, video = self._analyzer()
        assert analyzer.describe_bytes(b"raw", mime_type="image/jpeg", context="ctx") == "Image description: [x] y"
        image.describe_bytes.assert_called_once_with(b"raw", context="ctx", fallback="[Photo]")
        video.describe_bytes.assert_not_called()

    def test_video_routed_to_video_analyzer_with_video_fallback(self):
        analyzer, image, video = self._analyzer()
        assert analyzer.describe_bytes(b"raw", mime_type="video/quicktime", context="ctx") == "Video description: [x] y"
        video.describe_bytes.assert_called_once_with(b"raw", mime_type="video/quicktime", context="ctx", fallback="[Video]")
        image.describe_bytes.assert_not_called()

    def test_video_detected_from_filename_gets_mime_from_extension(self):
        analyzer, _, video = self._analyzer()
        analyzer.describe_bytes(b"raw", mime_type="application/octet-stream", filename="clip.MOV")
        assert video.describe_bytes.call_args[1]["mime_type"] == "video/quicktime"

    def test_video_detected_from_magic_bytes_passes_no_mime(self):
        analyzer, _, video = self._analyzer()
        analyzer.describe_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 4)
        assert video.describe_bytes.call_args[1]["mime_type"] is None

    def test_explicit_fallback_used_for_both_kinds(self):
        analyzer, image, video = self._analyzer()
        analyzer.describe_bytes(b"raw", mime_type="image/png", fallback="[Attachment]")
        analyzer.describe_bytes(b"raw", mime_type="video/mp4", fallback="[Attachment]")
        assert image.describe_bytes.call_args[1]["fallback"] == "[Attachment]"
        assert video.describe_bytes.call_args[1]["fallback"] == "[Attachment]"

    def test_unknown_kind_returns_placeholder_without_analyzing(self):
        analyzer, image, video = self._analyzer()
        assert analyzer.describe_bytes(b"%PDF-1.4", mime_type="application/pdf") == "[Attachment]"
        assert analyzer.describe_bytes(b"???", fallback="[File]") == "[File]"
        image.describe_bytes.assert_not_called()
        video.describe_bytes.assert_not_called()
