import base64
import io
import time
from typing import Generator
from google.genai.types import ThinkingLevel

from .AbstractLLM import AbstractLLM
from google import genai
from google.genai import types
from common.logging import trace, info
from common.ConfigManager import config

# Gemini rejects requests over 20 MB, so clips above this go through the Files API instead of an
# inline Blob (the margin leaves room for the prompt). The Files API takes files up to 2 GB.
INLINE_VIDEO_LIMIT = 19 * 1024 * 1024
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
# An uploaded video is PROCESSING for a few seconds before it can be referenced.
FILE_PROCESSING_TIMEOUT = 120
FILE_POLL_INTERVAL = 1.0


class GeminiLLM(AbstractLLM):
    """Talks to Google Gemini through the google-genai client, with native thinking levels."""

    _LOW_FLOOR_GENERATIONS = ('gemini-3.7', 'gemini-3.8')

    def __init__(self, model: str = "gemini-3.5-flash"):
        self.model = model
        self.client = None

    @property
    def api_key(self) -> str | None:
        """Read at every use, not captured at construction: a key stored from the chat (config_request)
        must reach the next request without a restart — initialize() builds the client per request."""
        return config.get_api_key('gemini')

    def name(self) -> str:
        return "Gemini"

    def api_key_name(self) -> str:
        return "api_keys.gemini"

    def missing_api_key(self) -> str | None:
        return None if self.api_key else self.api_key_name()

    def prefers_prepended_user_query(self) -> bool:
        return True

    def supports_native_thinking(self) -> bool:
        """Gemini supports native thinking via ThinkingLevel."""
        return True

    def _min_thinking_level(self) -> ThinkingLevel:
        """Lowest thinking level the model accepts.

        Gemini 3.7 and 3.8 Flash reject MINIMAL (400 INVALID_ARGUMENT) — their
        floor is LOW. Older Flash models (3, 3.5, 3.6) still accept MINIMAL.
        """
        if any(gen in self.model for gen in self._LOW_FLOOR_GENERATIONS):
            trace(f"{self.model} does not support MINIMAL thinking — using LOW as floor.")
            return ThinkingLevel.LOW
        return ThinkingLevel.MINIMAL

    def initialize(self):
        """Initialize the Google Generative AI client"""
        self.client = genai.Client(api_key=self.api_key)

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI/standard message format to Gemini format.

        Handles both string content and list content (Anthropic-format image blocks).

        Returns:
            Tuple of (system_instruction, gemini_messages)
        """
        gemini_messages = []
        system_instruction = None

        for msg in messages:
            if msg['role'] == 'system':
                # The Claude cache markers mean nothing to Gemini — strip them
                # so they don't reach the model as literal text.
                system_instruction = self.strip_cache_markers(msg['content'])
            elif msg['role'] in ('user', 'assistant'):
                role = 'model' if msg['role'] == 'assistant' else 'user'
                content = msg['content']
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if block.get('type') == 'image' and 'source' in block:
                            src = block['source']
                            parts.append(types.Part.from_bytes(
                                data=base64.b64decode(src['data']),
                                mime_type=src['media_type']
                            ))
                        elif block.get('type') == 'text':
                            parts.append({'text': block['text']})
                    gemini_messages.append({'role': role, 'parts': parts})
                else:
                    gemini_messages.append({'role': role, 'parts': [{'text': content}]})

        return system_instruction, gemini_messages

    def create_streaming_completion(self, messages: list[dict], thinking: bool = False) -> Generator[str, None, None]:
        self._ensure_initialized()

        system_instruction, gemini_messages = self._convert_messages(messages)

        # this is only used for longer queries from the user, we want it to be minimally slower than normal queries. Theres HIGH and MEDIUM here and it's great but doesn't fit what we need there (they're smart but 5s - so too slow)
        thinking_level = ThinkingLevel.LOW if thinking else self._min_thinking_level()
        config_params = {
            'thinking_config': types.ThinkingConfig(thinking_level=thinking_level)
        }

        if system_instruction:
            config_params['system_instruction'] = system_instruction

        generation_config = types.GenerateContentConfig(**config_params) if config_params else None

        response = self.client.models.generate_content_stream(
            model=self.model,
            contents=gemini_messages,
            config=generation_config
        )

        for chunk in response:
            if chunk.text:
                yield chunk.text

    def create_completion(self, messages: list[dict], thinking: bool = False) -> str:
        self._ensure_initialized()

        system_instruction, gemini_messages = self._convert_messages(messages)

        thinking_level = ThinkingLevel.HIGH if thinking else self._min_thinking_level()
        config_params = {
            'thinking_config': types.ThinkingConfig(thinking_level=thinking_level)
        }

        if system_instruction:
            config_params['system_instruction'] = system_instruction

        generation_config = types.GenerateContentConfig(**config_params) if config_params else None

        response = self.client.models.generate_content(
            model=self.model,
            contents=gemini_messages,
            config=generation_config
        )

        return response.text

    def create_completion_with_images(self, messages: list[dict], images: list) -> str:
        self._ensure_initialized()

        system_instruction, gemini_messages = self._convert_messages(messages)

        if gemini_messages and gemini_messages[-1]['role'] == 'user':
            parts = gemini_messages[-1]['parts']
            for img in images:
                if isinstance(img, (bytes, bytearray)):
                    mime = "image/png" if img[:4] == b'\x89PNG' else "image/jpeg"
                    parts.insert(-1, types.Part.from_bytes(data=img, mime_type=mime))
                else:
                    parts.insert(-1, types.Part.from_bytes(
                        data=base64.b64decode(img), mime_type="image/jpeg"
                    ))

        config_params = {}
        if system_instruction:
            config_params['system_instruction'] = system_instruction

        generation_config = types.GenerateContentConfig(**config_params) if config_params else None

        response = self.client.models.generate_content(
            model=self.model,
            contents=gemini_messages,
            config=generation_config
        )

        return self._text_or_empty(response, "image")

    @staticmethod
    def _text_or_empty(response, what: str) -> str:
        """The reply text, or "" when there is no text part at all (safety block, empty
        candidate). Callers treat "" as a parse failure and retry once, so a photo or clip is
        not written off on a single blocked reply — and a None never reaches their parsers."""
        if response.text is not None:
            return response.text
        candidate = next(iter(getattr(response, 'candidates', None) or []), None)
        feedback = getattr(response, 'prompt_feedback', None)
        info(f"Gemini: {what} request returned no text "
             f"(finish_reason={getattr(candidate, 'finish_reason', None)}, "
             f"block_reason={getattr(feedback, 'block_reason', None)})")
        return ""

    def supports_video(self) -> bool:
        return True

    def max_video_bytes(self) -> int:
        return MAX_VIDEO_BYTES

    def think_with_video(self, system_message: str, user_message: str, video_bytes: bytes,
                         mime_type: str, fps: float | None = None) -> str:
        """One-shot video request. Clips up to INLINE_VIDEO_LIMIT are sent inline (bytes + mime
        type); larger ones are uploaded through the Files API first and deleted afterwards. Either
        way the clip precedes the user text. `fps` overrides Gemini's default 1 frame/s sampling;
        None leaves it to the model."""
        return self._retry(lambda: self._create_completion_with_video(
            system_message, user_message, video_bytes, mime_type, fps))

    def _create_completion_with_video(self, system_message: str, user_message: str, video_bytes: bytes,
                                      mime_type: str, fps: float | None) -> str:
        self._ensure_initialized()

        video_metadata = types.VideoMetadata(fps=fps) if fps else None
        size_mb = len(video_bytes) / 1024 / 1024
        uploaded = None
        if len(video_bytes) > INLINE_VIDEO_LIMIT:
            uploaded = self._upload_video(video_bytes, mime_type)
            video_part = types.Part(
                file_data=types.FileData(file_uri=uploaded.uri, mime_type=mime_type),
                video_metadata=video_metadata,
            )
        else:
            video_part = types.Part(
                inline_data=types.Blob(data=video_bytes, mime_type=mime_type),
                video_metadata=video_metadata,
            )
        contents = [{'role': 'user', 'parts': [video_part, {'text': user_message}]}]

        system_instruction = self.strip_cache_markers(system_message)
        generation_config = types.GenerateContentConfig(system_instruction=system_instruction) \
            if system_instruction else None

        info(f"Gemini: sending video ({size_mb:.1f} MB, {mime_type}"
             f"{f', {fps} fps' if fps else ''}, {'Files API' if uploaded else 'inline'}) to {self.model}")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=generation_config
            )
        finally:
            if uploaded is not None:
                self._delete_file(uploaded)

        usage = getattr(response, 'usage_metadata', None)
        if usage is not None:
            info(f"Gemini: video request used {getattr(usage, 'prompt_token_count', '?')} prompt tokens, "
                 f"{getattr(usage, 'total_token_count', '?')} total")
        return self._text_or_empty(response, "video")

    def _upload_video(self, video_bytes: bytes, mime_type: str):
        """Upload a clip through the Files API and wait until Gemini has processed it. Returns the
        File; raises when the upload fails, processing fails, or it takes longer than
        FILE_PROCESSING_TIMEOUT. The caller deletes the file when done with it."""
        size_mb = len(video_bytes) / 1024 / 1024
        info(f"Gemini: uploading video ({size_mb:.1f} MB, {mime_type}) through the Files API")
        started = time.monotonic()
        uploaded = self.client.files.upload(
            file=io.BytesIO(video_bytes), config=types.UploadFileConfig(mime_type=mime_type))
        upload_seconds = time.monotonic() - started
        trace(f"Gemini: uploaded {uploaded.name} in {upload_seconds:.1f}s, state {self._file_state(uploaded)}")

        deadline = started + upload_seconds + FILE_PROCESSING_TIMEOUT
        while self._file_state(uploaded) == "PROCESSING":
            if time.monotonic() > deadline:
                self._delete_file(uploaded)
                raise TimeoutError(f"Gemini: {uploaded.name} still processing after {FILE_PROCESSING_TIMEOUT}s")
            time.sleep(FILE_POLL_INTERVAL)
            uploaded = self.client.files.get(name=uploaded.name)

        state = self._file_state(uploaded)
        if state != "ACTIVE":
            self._delete_file(uploaded)
            raise RuntimeError(f"Gemini: {uploaded.name} ended in state {state}")
        info(f"Gemini: {uploaded.name} active after {time.monotonic() - started:.1f}s "
             f"(upload {upload_seconds:.1f}s)")
        return uploaded

    @staticmethod
    def _file_state(file) -> str:
        state = getattr(file, 'state', None)
        return getattr(state, 'name', None) or str(state or "").upper()

    def _delete_file(self, file):
        """Best effort: files expire after 48 h anyway, but the project store is capped at 20 GB."""
        try:
            self.client.files.delete(name=file.name)
            trace(f"Gemini: deleted {file.name}")
        except Exception as e:
            trace(f"Gemini: could not delete {file.name}: {e}")
