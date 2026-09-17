import io
import json
import os
import re
import subprocess
import tempfile
import threading
from typing import Optional

from PIL import Image

from common.logging import info, error, trace, fine

DESCRIPTION_PROMPT_SYSTEM = """You are an image analyst. Analyze the provided image and return a JSON object with these keys:
- "short_description": A concise 1-sentence description (max 50 chars)
- "detailed_description": A rich description covering subjects, setting, mood, colors, and notable details. Include any visible text, landmarks, or activities.

{context}

Return ONLY the JSON object, no other text."""

DESCRIPTION_PROMPT_USER = "Analyze this image and return the JSON object as described."

MAX_DIMENSION = 1024
MAX_IMAGE_SIZE = 5 * 1024 * 1024


def resize_to_jpeg(raw_bytes: bytes, max_dim: int = MAX_DIMENSION) -> Optional[bytes]:
    try:
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception:
        trace("ImageAnalyzer: Pillow can't open image, falling back to sips")
        raw_bytes = convert_via_sips(raw_bytes)
        if not raw_bytes:
            return None
        try:
            img = Image.open(io.BytesIO(raw_bytes))
        except Exception as e:
            error(f"ImageAnalyzer: resize failed after sips conversion: {e}")
            return None

    try:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        if "truncated" in str(e):
            raise
        error(f"ImageAnalyzer: resize failed: {e}")
        return None


def convert_via_sips(raw_bytes: bytes) -> Optional[bytes]:
    """Convert raw image bytes to JPEG using macOS sips (handles HEIC, RAW, etc.)."""
    tmp_in = None
    tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(raw_bytes)
            tmp_in = f.name
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_out = f.name

        subprocess.run(
            ["sips", "-s", "format", "jpeg", tmp_in, "--out", tmp_out],
            capture_output=True, timeout=30, check=True,
        )
        with open(tmp_out, "rb") as f:
            result = f.read()
        info(f"ImageAnalyzer: sips conversion succeeded ({len(raw_bytes)} -> {len(result)} bytes)")
        return result
    except Exception as e:
        error(f"ImageAnalyzer: sips conversion failed: {e}")
        return None
    finally:
        for path in (tmp_in, tmp_out):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


class ImageAnalyzer:
    """Describes images with the vision-tier LLM.

    The LLM is created lazily from the `ai.vision` config on first use, or injected through
    the constructor (tests, or a caller that already holds a vision-capable LLM). Use
    `image_analyzer()` for the process-wide instance so every integration shares one LLM.
    """

    def __init__(self, llm=None):
        self._llm = llm
        self._llm_lock = threading.Lock()

    @property
    def llm(self):
        if self._llm is None:
            with self._llm_lock:
                if self._llm is None:
                    from ..llm import create_llm, parse_llm_config
                    name, model = parse_llm_config('vision')
                    llm = create_llm(name, model)
                    llm.initialize()
                    self._llm = llm
                    trace(f"ImageAnalyzer: initialized {name} ({model}) for photo analysis")
        return self._llm

    def describe_bytes(self, raw_bytes: bytes, context: str = "", fallback: str = "[Photo]") -> str:
        """Full image-analysis pipeline: size check → resize to JPEG → vision LLM → formatted
        description. Returns *fallback* on any skip or failure, so callers never have to handle
        the intermediate steps themselves."""
        try:
            size = len(raw_bytes)
            if size > MAX_IMAGE_SIZE:
                info(f"ImageAnalyzer: image too large ({size / 1024 / 1024:.1f} MB), skipping analysis")
                return fallback

            jpeg_bytes = resize_to_jpeg(raw_bytes)
            if not jpeg_bytes:
                info("ImageAnalyzer: could not convert image to JPEG, skipping analysis")
                return fallback

            result = self.analyze(jpeg_bytes, context=context)
            if not result:
                info("ImageAnalyzer: vision LLM returned no description")
                return fallback

            info(f"ImageAnalyzer: analyzed image — {result['short_description']}")
            return self.format_description(result)
        except Exception as e:
            error(f"ImageAnalyzer: image analysis failed: {e}")
            return fallback

    def analyze(self, jpeg_bytes: bytes, context: str = "") -> Optional[dict]:
        context_block = f"# Additional context\n{context}" if context else ""
        system = DESCRIPTION_PROMPT_SYSTEM.format(context=context_block)

        for attempt in range(3):
            try:
                response = self.llm.think(
                    system_message=system,
                    user_message=DESCRIPTION_PROMPT_USER,
                    images=[jpeg_bytes],
                )
                return self._parse_response(response)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                if attempt < 2:
                    trace(f"ImageAnalyzer: LLM response parse failed, retrying: {e}")
                    continue
                error(f"ImageAnalyzer: analysis failed after retry: {e}")
                return None
            except Exception as e:
                error(f"ImageAnalyzer: LLM call failed: {e}")
                return None
        return None

    @staticmethod
    def format_description(result: dict) -> str:
        return f"Image description: [{result['short_description']}] {result['detailed_description']}"

    @staticmethod
    def _parse_response(response: str) -> dict:
        return parse_description_json(response, ("short_description", "detailed_description"), "ImageAnalyzer")


def parse_description_json(response: str, required_keys: tuple[str, ...], tag: str) -> dict:
    """Pull the first JSON object out of a description reply and check it carries *required_keys*.
    Raises ValueError / KeyError / JSONDecodeError so callers can retry the LLM call."""
    match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
    if not match:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if not match:
            trace(f"{tag}: no JSON found in response ({len(response)} chars): {response[:200]}")
            raise ValueError("No JSON object found in LLM response")
    data = json.loads(match.group())
    for key in required_keys:
        if key not in data:
            raise KeyError(f"Missing required key: {key}")
    fine(f"{tag}: '{data['short_description']}'")
    return data


_default = ImageAnalyzer()


def image_analyzer() -> ImageAnalyzer:
    """The process-wide analyzer; all integrations share its lazily created vision LLM."""
    return _default
