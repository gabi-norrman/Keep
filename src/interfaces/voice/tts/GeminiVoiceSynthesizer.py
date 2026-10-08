import array
import json
import queue
import re
import sys
import threading
from typing import Iterator, List

from .VoiceSynthesizer import VoiceSynthesizer
from assistant.integration.Integration import Integration
from common.logging import error, info, trace
from common.ConfigManager import config


class GeminiVoiceSynthesizer(VoiceSynthesizer, Integration):
    """A voice from Google Gemini TTS, with a tone a prompt can steer.

    Speaks Keep's replies with the Gemini models of Google Cloud TTS, whose tone can be steered
    by a prompt.

    Streams over a bidirectional stream."""

    # Can render emotion tags, but the feature is currently disabled at the prompt
    # level (see VoiceSynthesizer.SUPPORTS_EMOTION_TAGS) — it was a bit wonky.
    SUPPORTS_EMOTION_TAGS = True

    CONFIG_SECTION = "gemini"

    # Google's Gemini/Chirp voices render noticeably louder than the ElevenLabs
    # baseline; scale output PCM down to the same level. Mirrors GEMINI_GAIN in the
    # thinking-sample generator (Tools/GenerateThinkingSamples). 1.0 disables scaling.
    OUTPUT_GAIN = 0.5

    @property
    def name(self) -> str:
        return "Gemini TTS"

    @property
    def config_keys(self) -> List[str]:
        return ["google_service_account"]

    def check_health(self) -> bool:
        try:
            from google.oauth2 import service_account
            service_account_info = config.get('google_service_account')
            if isinstance(service_account_info, str):
                service_account_info = json.loads(service_account_info)
            service_account.Credentials.from_service_account_info(service_account_info)
            return True
        except Exception:
            return False

    def __init__(self):
        VoiceSynthesizer.__init__(self)
        Integration.__init__(self)
        self.model_id: str = "gemini-3.1-flash-tts-preview"
        self.sample_rate: int = 24000
        self.synthesis_thread = None
        self._client = None
        self._gain_leftover = b""  # odd trailing byte carried between PCM chunks

    @staticmethod
    def voice_id() -> str:
        return VoiceSynthesizer.configured_voice("gemini", "Sulafat")

    def _get_client(self):
        """Lazily initialize the TTS client using service account from config."""
        if self._client is None:
            from google.cloud import texttospeech
            from google.oauth2 import service_account

            service_account_info = config.get('google_service_account')
            if isinstance(service_account_info, str):
                service_account_info = json.loads(service_account_info)

            credentials = service_account.Credentials.from_service_account_info(service_account_info)
            self._client = texttospeech.TextToSpeechClient(credentials=credentials)
        return self._client

    def start(self, llm_text_stream: Iterator[str]) -> Iterator[bytes]:
        """Set up the audio stream with text chunks from the LLM."""
        self.llm_text_stream = llm_text_stream
        self.is_closed = False
        self._gain_leftover = b""
        self._clear_queue(self.audio_queue)
        self._clear_queue(self.text_queue)

        threading.Thread(target=self._feed_text_to_queue, daemon=True).start()

        self.synthesis_thread = threading.Thread(target=self._run_synthesis, daemon=True)
        self.synthesis_thread.start()

        self.audio_stream = self._audio_generator()
        return self.audio_stream

    # Next sentence: text up to and including a terminator (plus any closing
    # quote/bracket) followed by whitespace.
    _SENTENCE_RE = re.compile(r'.*?[.!?…]+["\')\]]?\s', re.DOTALL)

    # Flush a segment even without a sentence boundary once it reaches this length,
    # so a run-on (or a stream that never punctuates) can't stall playback.
    MAX_SEGMENT_CHARS = 200

    def _next_segment(self, buffer: str, force: bool = False):
        """Split the next ready segment off the front of buffer.

        Returns (segment, remainder). segment is None when no complete sentence is
        available yet and the buffer is under the cap - unless force is set (used at
        end of stream to flush whatever's left)."""
        match = self._SENTENCE_RE.match(buffer)
        if match:
            idx = match.end()
            return buffer[:idx], buffer[idx:]
        if len(buffer) >= self.MAX_SEGMENT_CHARS:
            cut = buffer.rfind(' ', 0, self.MAX_SEGMENT_CHARS)
            if cut <= 0:
                cut = self.MAX_SEGMENT_CHARS
            return buffer[:cut], buffer[cut:]
        if force and buffer.strip():
            return buffer, ""
        return None, buffer

    def _segments(self) -> Iterator[str]:
        """Consume processed text from text_queue and yield sentence-sized segments.

        Each segment is synthesized in its own short-lived stream, so Google sees
        end-of-input quickly and emits audio for it without waiting for the whole
        reply - which is what lets speech start after the first sentence instead of
        after ~70% of the text."""
        buffer = ""
        while True:
            try:
                chunk = self.text_queue.get(timeout=0.5)
            except queue.Empty:
                if self.is_closed:
                    break
                continue

            if chunk is None:
                break
            self.text_queue.task_done()
            buffer += chunk

            while True:
                segment, buffer = self._next_segment(buffer)
                if segment is None:
                    break
                if segment.strip():
                    yield segment

        segment, _ = self._next_segment(buffer, force=True)
        if segment and segment.strip():
            yield segment

    def _build_streaming_config(self):
        """Build the opening config request (voice selection). Subclasses override
        to target a different model/voice-naming scheme."""
        from google.cloud import texttospeech

        # noinspection PyTypeChecker
        return texttospeech.StreamingSynthesizeRequest(
            streaming_config=texttospeech.StreamingSynthesizeConfig(
                voice=texttospeech.VoiceSelectionParams(
                    name=self.voice_id(),
                    language_code=self.effective_language(),
                    model_name=self.model_id
                )
            )
        )

    def _build_input_request(self, text: str, is_first: bool):
        """Build a text input request. The Gemini model takes a style `prompt`
        alongside the first chunk; subclasses override for other input schemas."""
        from google.cloud import texttospeech

        if is_first:
            # noinspection PyTypeChecker
            return texttospeech.StreamingSynthesizeRequest(
                input=texttospeech.StreamingSynthesisInput(
                    text=text,
                    prompt="Read in a very professional tone, keep a fairly high pace: "
                )
            )
        # noinspection PyTypeChecker
        return texttospeech.StreamingSynthesizeRequest(
            input=texttospeech.StreamingSynthesisInput(text=text)
        )

    def _synthesize_segment(self, client, text: str):
        """Synthesize one text segment as its own short streaming call, pushing the
        resulting audio onto audio_queue in order. The input stream carries just this
        segment then closes, so Google emits its audio promptly."""
        def request_generator():
            yield self._build_streaming_config()
            yield self._build_input_request(text, is_first=True)

        for response in client.streaming_synthesize(request_generator()):
            if self.is_closed:
                break
            if response.audio_content:
                self.audio_queue.put(self._apply_gain(response.audio_content))

    def _apply_gain(self, pcm: bytes) -> bytes:
        """Scale a streamed 16-bit mono PCM chunk by OUTPUT_GAIN, clamping to avoid
        wrap-around. Any odd trailing byte is carried to the next chunk so 16-bit
        sample boundaries stay aligned across the stream."""
        if self.OUTPUT_GAIN == 1.0:
            return pcm
        data = self._gain_leftover + pcm
        cut = len(data) - (len(data) % 2)
        self._gain_leftover = data[cut:]
        samples = array.array("h")
        samples.frombytes(data[:cut])
        gain = self.OUTPUT_GAIN
        for i, s in enumerate(samples):
            samples[i] = max(-32768, min(32767, int(s * gain)))
        if sys.byteorder == "big":
            samples.byteswap()
        return samples.tobytes()

    def _run_synthesis(self):
        """Synthesize the reply segment-by-segment in a background thread.

        Segments are synthesized in order and their audio queued as it arrives.
        Playback drains audio_queue in real time (slower than synthesis), so later
        segments are produced while earlier ones are still playing."""
        try:
            client = None
            count = 0
            for segment in self._segments():
                if self.is_closed:
                    break
                if client is None:
                    client = self._get_client()
                    trace(f"{self.name}: first segment ready, opening synthesis")
                count += 1
                trace(f"{self.name}: synthesizing segment {count} ({len(segment.strip())} chars)")
                self._synthesize_segment(client, segment)

            if count == 0:
                info(f"{self.name}: no text to synthesize, nothing to do")
            self.audio_queue.put(None)
        except Exception as e:
            error(f"{self.name} synthesis error: {e}")
            self.audio_queue.put(None)

    def _join_threads(self):
        if self.synthesis_thread and self.synthesis_thread.is_alive():
            self.synthesis_thread.join(timeout=2)
