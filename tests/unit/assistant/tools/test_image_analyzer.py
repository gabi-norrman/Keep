import io
import json
import os
from unittest.mock import patch, Mock

import pytest
from PIL import Image

from assistant.tools.ImageAnalyzer import (
    ImageAnalyzer, image_analyzer, resize_to_jpeg, convert_via_sips,
)


def _png(size=(100, 100), mode="RGB", color="red") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _analyzer_with_reply(*replies) -> tuple[ImageAnalyzer, Mock]:
    llm = Mock()
    if len(replies) == 1:
        llm.think.return_value = replies[0]
    else:
        llm.think.side_effect = list(replies)
    return ImageAnalyzer(llm=llm), llm


@pytest.mark.unit
class TestResizeToJpeg:
    def test_resize_small_image(self):
        result = resize_to_jpeg(_png())
        assert result is not None
        assert result[:2] == b'\xff\xd8'

    def test_resize_large_image_scales_down(self):
        result = resize_to_jpeg(_png(size=(1600, 1200), color="blue"))
        assert result is not None
        assert max(Image.open(io.BytesIO(result)).size) == 1024

    def test_resize_rgba_converted_to_rgb(self):
        assert resize_to_jpeg(_png(size=(200, 200), mode="RGBA", color=(255, 0, 0, 128))) is not None

    def test_resize_invalid_bytes_tries_sips_fallback(self):
        with patch('assistant.tools.ImageAnalyzer.convert_via_sips', return_value=None) as mock_sips:
            result = resize_to_jpeg(b"not an image")
        mock_sips.assert_called_once_with(b"not an image")
        assert result is None

    def test_resize_sips_fallback_success(self):
        buf = io.BytesIO()
        Image.new("RGB", (200, 200), color=(0, 255, 0)).save(buf, format="JPEG")
        with patch('assistant.tools.ImageAnalyzer.convert_via_sips', return_value=buf.getvalue()):
            result = resize_to_jpeg(b"heic binary data")
        assert result is not None
        assert Image.open(io.BytesIO(result)).format == "JPEG"


@pytest.mark.unit
class TestConvertViaSips:
    def test_success(self):
        jpeg_data = b"\xff\xd8\xff\xe0converted_jpeg"

        def fake_sips(cmd, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(jpeg_data)

        with patch('assistant.tools.ImageAnalyzer.subprocess.run', side_effect=fake_sips):
            assert convert_via_sips(b"heic raw bytes") == jpeg_data

    def test_failure_returns_none(self):
        with patch('assistant.tools.ImageAnalyzer.subprocess.run',
                   side_effect=Exception("sips not found")):
            assert convert_via_sips(b"some bytes") is None

    def test_cleans_up_temp_files(self):
        created_paths = []

        def fake_sips(cmd, **kwargs):
            created_paths.append(cmd[4])   # tmp_in
            created_paths.append(cmd[-1])  # tmp_out
            with open(cmd[-1], 'wb') as f:
                f.write(b"\xff\xd8jpeg")

        with patch('assistant.tools.ImageAnalyzer.subprocess.run', side_effect=fake_sips):
            convert_via_sips(b"test data")

        for path in created_paths:
            assert not os.path.exists(path), f"Temp file not cleaned up: {path}"

    def test_cleans_up_on_failure(self):
        with patch('assistant.tools.ImageAnalyzer.subprocess.run', side_effect=Exception("fail")):
            convert_via_sips(b"test data")


@pytest.mark.unit
class TestFormatDescription:
    def test_formats_correctly(self):
        result = ImageAnalyzer.format_description({
            "short_description": "A sunset",
            "detailed_description": "Beautiful sunset over the ocean with orange and pink hues."
        })
        assert result == "Image description: [A sunset] Beautiful sunset over the ocean with orange and pink hues."


@pytest.mark.unit
class TestParseResponse:
    def test_parses_valid_json(self):
        response = '{"short_description": "A cat", "detailed_description": "A fluffy cat sitting on a sofa."}'
        result = ImageAnalyzer._parse_response(response)
        assert result["short_description"] == "A cat"
        assert result["detailed_description"] == "A fluffy cat sitting on a sofa."

    def test_parses_json_with_surrounding_text(self):
        response = 'Here is the analysis:\n{"short_description": "A dog", "detailed_description": "A dog playing."}\nDone.'
        assert ImageAnalyzer._parse_response(response)["short_description"] == "A dog"

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            ImageAnalyzer._parse_response('{"short_description": "A cat"}')

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            ImageAnalyzer._parse_response("No JSON here at all")

    def test_invalid_json_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            ImageAnalyzer._parse_response("{broken json")


@pytest.mark.unit
class TestAnalyze:
    def test_successful_analysis(self):
        analyzer, llm = _analyzer_with_reply(
            '{"short_description": "A park", "detailed_description": "People in a park."}')
        result = analyzer.analyze(b"fake_jpeg", context="test")
        assert result is not None
        assert result["short_description"] == "A park"
        llm.think.assert_called_once()

    def test_retries_on_parse_failure(self):
        analyzer, llm = _analyzer_with_reply(
            "not json",
            '{"short_description": "Retry", "detailed_description": "Worked on retry."}')
        result = analyzer.analyze(b"fake_jpeg")
        assert result is not None
        assert result["short_description"] == "Retry"
        assert llm.think.call_count == 2

    def test_returns_none_after_three_parse_failures(self):
        analyzer, llm = _analyzer_with_reply("never valid json")
        assert analyzer.analyze(b"fake_jpeg") is None
        assert llm.think.call_count == 3

    def test_returns_none_on_llm_exception(self):
        analyzer, llm = _analyzer_with_reply("unused")
        llm.think.side_effect = RuntimeError("API down")
        assert analyzer.analyze(b"fake_jpeg") is None

    def test_context_included_in_system_prompt(self):
        analyzer, llm = _analyzer_with_reply('{"short_description": "X", "detailed_description": "Y"}')
        analyzer.analyze(b"fake_jpeg", context="Sent by Alice")
        assert "Sent by Alice" in llm.think.call_args[1]["system_message"]

    def test_empty_context_no_section(self):
        analyzer, llm = _analyzer_with_reply('{"short_description": "X", "detailed_description": "Y"}')
        analyzer.analyze(b"fake_jpeg", context="")
        assert "Additional context" not in llm.think.call_args[1]["system_message"]


@pytest.mark.unit
class TestVisionLlm:
    def test_creates_from_vision_tier_on_first_use(self):
        # create_llm wraps the backend in a VaultLLM, so the concrete class sits at .llm.llm.
        from assistant.llm.VaultLLM import VaultLLM
        mock_llm = Mock()
        with patch('assistant.llm.ClaudeLLM.ClaudeLLM', return_value=mock_llm) as mock_cls:
            llm = ImageAnalyzer().llm
            assert isinstance(llm, VaultLLM) and llm.llm is mock_llm
            from assistant.llm import _TIER_DEFAULTS
            mock_cls.assert_called_once_with(model=_TIER_DEFAULTS['vision'][1])
            mock_llm.initialize.assert_called_once()

    def test_caches_llm_across_calls(self):
        with patch('assistant.llm.ClaudeLLM.ClaudeLLM', return_value=Mock()) as mock_cls:
            analyzer = ImageAnalyzer()
            assert analyzer.llm is analyzer.llm
            mock_cls.assert_called_once()

    def test_injected_llm_is_used_as_is(self):
        injected = Mock()
        with patch('assistant.llm.ClaudeLLM.ClaudeLLM') as mock_cls:
            assert ImageAnalyzer(llm=injected).llm is injected
        mock_cls.assert_not_called()
        injected.initialize.assert_not_called()

    def test_image_analyzer_is_process_wide_singleton(self):
        assert image_analyzer() is image_analyzer()
        assert isinstance(image_analyzer(), ImageAnalyzer)


@pytest.mark.unit
class TestDescribeBytes:
    def test_full_pipeline_success(self):
        analyzer, _ = _analyzer_with_reply('{"short_description": "A cat", "detailed_description": "A fluffy cat."}')
        with patch('assistant.tools.ImageAnalyzer.resize_to_jpeg', return_value=b'jpeg'):
            result = analyzer.describe_bytes(b"raw", context="from Alice")
        assert result == "Image description: [A cat] A fluffy cat."

    def test_context_passed_to_analyze(self):
        analyzer = ImageAnalyzer(llm=Mock())
        with patch('assistant.tools.ImageAnalyzer.resize_to_jpeg', return_value=b'jpeg'), \
             patch.object(analyzer, 'analyze', return_value={
                 "short_description": "X", "detailed_description": "Y"}) as mock_analyze:
            analyzer.describe_bytes(b"raw", context="Sent by Bob")
        assert mock_analyze.call_args[1]["context"] == "Sent by Bob"

    def test_too_large_returns_fallback(self):
        analyzer = ImageAnalyzer(llm=Mock())
        with patch('assistant.tools.ImageAnalyzer.MAX_IMAGE_SIZE', 10):
            assert analyzer.describe_bytes(b"x" * 100) == "[Photo]"
        analyzer.llm.think.assert_not_called()

    def test_resize_failure_returns_fallback(self):
        analyzer = ImageAnalyzer(llm=Mock())
        with patch('assistant.tools.ImageAnalyzer.resize_to_jpeg', return_value=None):
            assert analyzer.describe_bytes(b"raw") == "[Photo]"
        analyzer.llm.think.assert_not_called()

    def test_analyze_returns_none_uses_fallback(self):
        analyzer, _ = _analyzer_with_reply("never valid json")
        with patch('assistant.tools.ImageAnalyzer.resize_to_jpeg', return_value=b'jpeg'):
            assert analyzer.describe_bytes(b"raw") == "[Photo]"

    def test_exception_returns_fallback(self):
        analyzer = ImageAnalyzer(llm=Mock())
        with patch('assistant.tools.ImageAnalyzer.resize_to_jpeg', side_effect=RuntimeError("boom")):
            assert analyzer.describe_bytes(b"raw", fallback="[Image]") == "[Image]"

    def test_custom_fallback(self):
        analyzer = ImageAnalyzer(llm=Mock())
        with patch('assistant.tools.ImageAnalyzer.resize_to_jpeg', return_value=None):
            assert analyzer.describe_bytes(b"raw", fallback="[Image]") == "[Image]"
