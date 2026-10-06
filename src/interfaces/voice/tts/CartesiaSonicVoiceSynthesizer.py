import json
import queue
import threading
from typing import Iterator, List

import requests as _requests

from .WebSocketVoiceSynthesizer import WebSocketVoiceSynthesizer
from assistant.integration.Integration import Integration
from common.logging import error, info
from common.ConfigManager import config


class CartesiaSonicVoiceSynthesizer(WebSocketVoiceSynthesizer, Integration):
    """A voice from Cartesia Sonic.

    Speaks Keep's replies with Cartesia's Sonic TTS, streamed while the answer is still being
    written.

    Streams over the websocket API."""

    # Can render emotion tags, but the feature is currently disabled at the prompt
    # level (see VoiceSynthesizer.SUPPORTS_EMOTION_TAGS) — it was a bit wonky.
    SUPPORTS_EMOTION_TAGS = True

    CONFIG_SECTION = "cartesia"

    @property
    def name(self) -> str:
        return "Cartesia Sonic TTS"

    @property
    def config_keys(self) -> List[str]:
        return ["api_keys.cartesia"]

    def check_health(self) -> bool:
        return _requests.get("https://api.cartesia.ai/voices",
                             headers={"X-API-Key": self.api_key, "Cartesia-Version": "2024-06-10"},
                             timeout=10).status_code == 200

    def __init__(self, volume: float = 0.8):
        WebSocketVoiceSynthesizer.__init__(self)
        Integration.__init__(self)
        self.api_key: str = config.get_api_key('cartesia')
        self.model_id: str = "sonic-3"
        self.sample_rate: int = 24000
        self.volume: float = max(0.5, min(2.0, volume))

        self.context_id = f"floxy-{id(self)}"
        self._saw_done = False
        self._last_chunk_len = 0

    @staticmethod
    def voice_id() -> str:
        # The stock voice: female, little emotion (the emotion tags do nothing on it, laughter works)
        return WebSocketVoiceSynthesizer.configured_voice("cartesia", "829ccd10-f8b3-43cd-b8a0-4aeaa81f3b30")

    def _get_ws_url(self) -> str:
        return "wss://api.cartesia.ai/tts/websocket"

    def _get_ws_headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Cartesia-Version": "2024-06-10"
        }

    def start(self, llm_text_stream: Iterator[str]) -> Iterator[bytes]:
        """Set up the audio stream with text chunks from the LLM."""
        self.llm_text_stream = llm_text_stream
        self._saw_done = False
        self._last_chunk_len = 0
        self._clear_queue(self.audio_queue)

        if not self.is_connected:
            self.connect()

        threading.Thread(
            target=self._feed_text_to_queue,
            kwargs={"text_preprocessor": lambda chunk: chunk.replace("–", ",")},
            daemon=True
        ).start()
        self.audio_stream = self._audio_generator()
        return self.audio_stream

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "chunk":
                audio_data = data.get("data")
                if audio_data:
                    import base64
                    audio_bytes = base64.b64decode(audio_data)
                    self._last_chunk_len = len(audio_bytes)
                    self.audio_queue.put(audio_bytes)

                if data.get("done", False):
                    self._saw_done = True
                    import time
                    time.sleep(1.0)
                    self.audio_queue.put(None)

            elif msg_type == "done":
                self._saw_done = True
                import time
                time.sleep(1.0)
                self.audio_queue.put(None)

            elif msg_type == "error":
                error(f"Cartesia API error: {data.get('error', 'Unknown error')}")

        except Exception as e:
            error(f"Error processing message: {e}")

    def _on_error(self, ws, e):
        """Handle WebSocket errors"""
        error_msg = str(e)
        if "Connection to remote host was lost" not in error_msg and "object has no attribute 'sock'" not in error_msg and "EOF occurred in violation of protocol" not in error_msg:
            info(f"WebSocket error: {e}")
        self.audio_queue.put(None)

    def _on_open(self, ws):
        """Handle WebSocket connection open"""
        self.is_connected = True
        threading.Thread(target=self._feed_text_thread, daemon=True).start()

    def _create_message(self, transcript: str, continue_streaming: bool) -> dict:
        """Create a Cartesia API message with the given transcript and continue flag"""
        return {
            "model_id": self.model_id,
            "transcript": transcript,
            "voice": {
                "mode": "id",
                "id": self.voice_id(),
                "experimental_controls": {
                    "speed": "fastest"
                }
            },
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate
            },
            "generation_config": {
                "volume": self.volume
            },
            "context_id": self.context_id,
            "continue": continue_streaming
        }

    def _feed_text_thread(self):
        """Thread that feeds text to the WebSocket"""
        try:
            import re
            first_chunk = True
            buffered_text = ""
            has_sent_any_content = False

            while not self.is_closed:
                try:
                    text_chunk = self.text_queue.get(timeout=0.5)

                    if text_chunk is None:
                        if buffered_text.strip():
                            message = self._create_message(buffered_text, continue_streaming=True)
                            self.ws.send(json.dumps(message))
                            has_sent_any_content = True

                        if has_sent_any_content:
                            message = self._create_message("", continue_streaming=False)
                            self.ws.send(json.dumps(message))
                        else:
                            # Nothing to synthesize (e.g. the LLM reply was a command
                            # call with no spoken text). Cartesia will never send a
                            # `done` because we never opened a context, so signal
                            # end-of-audio ourselves — otherwise the request thread
                            # deadlocks forever waiting on audio_queue.
                            info("No spoken text to synthesize, ending audio stream immediately")
                            self.audio_queue.put(None)
                        break

                    if not text_chunk.strip():
                        self.text_queue.task_done()
                        continue

                    buffered_text += text_chunk

                    # Check if there's an incomplete tag at the end
                    last_open = buffered_text.rfind('<')
                    last_close = buffered_text.rfind('>')
                    has_incomplete_tag = last_open > last_close

                    if has_incomplete_tag:
                        self.text_queue.task_done()
                        continue

                    # Check if buffer ends with a complete tag (meaning no text after the tag yet)
                    if buffered_text.rstrip().endswith('/>') or buffered_text.rstrip().endswith('>'):
                        text_after_last_tag = buffered_text[last_close + 1:].strip() if last_close >= 0 else buffered_text.strip()
                        if not text_after_last_tag:
                            self.text_queue.task_done()
                            continue

                    if first_chunk:
                        text_without_tags = re.sub(r'<[^>]*>', '', buffered_text)
                        if not any(c.isalnum() for c in text_without_tags):
                            self.text_queue.task_done()
                            continue

                        self.context_id = f"floxy-{threading.get_ident()}"
                        first_chunk = False

                    if buffered_text.strip():
                        message = self._create_message(buffered_text, continue_streaming=True)
                        self.ws.send(json.dumps(message))
                        has_sent_any_content = True

                    buffered_text = ""
                    self.text_queue.task_done()

                except queue.Empty:
                    continue
        except Exception as e:
            if "Connection is already closed" not in str(e):
                error(f"Error in feed text thread: {e}")
        finally:
            self.text_queue.put(None)
