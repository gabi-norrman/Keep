import queue
import threading
from typing import Iterator, List

import ormsgpack
import requests as _requests
from websocket import ABNF

from .WebSocketVoiceSynthesizer import WebSocketVoiceSynthesizer
from assistant.integration.Integration import Integration
from common.logging import error, info, trace
from common.ConfigManager import config


class FishAudioVoiceSynthesizer(WebSocketVoiceSynthesizer, Integration):
    """A voice from Fish Audio's TTS, streamed as the answer is written.

    Speaks Keep's replies with Fish Audio's TTS, streamed while the answer is still being
    written.

    Streams over the websocket API."""

    # Fish S2.x renders bracket markers ([whisper], [laugh], ...) as audio, so stray
    # tags must reach the API instead of being stripped by the base class. The feature
    # is disabled at the prompt level (see VoiceSynthesizer.SUPPORTS_EMOTION_TAGS).
    SUPPORTS_EMOTION_TAGS = True

    CONFIG_SECTION = "fish"

    @property
    def name(self) -> str:
        return "Fish Audio TTS"

    @property
    def config_keys(self) -> List[str]:
        return ["api_keys.fish_audio"]

    def check_health(self) -> bool:
        return _requests.get("https://api.fish.audio/wallet/self/api-credit",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             timeout=10).status_code == 200

    def __init__(self):
        WebSocketVoiceSynthesizer.__init__(self)
        Integration.__init__(self)
        # Tolerate a missing key: get_instance() runs at registration time, before
        # IntegrationMonitor can mark this integration NOT_CONFIGURED.
        self.api_key: str = config.get('api_keys.fish_audio', '')
        cfg = config.get('tts.fish', {}) or {}
        self.model_id: str = cfg.get('model', 's2.1-pro-free')
        self.latency: str = "balanced"
        self.chunk_length: int = 100  # API minimum: fastest first audio
        self.speed: float = 1.0
        self.sample_rate: int = 24000  # pipeline-native: s16le mono 24 kHz

    @staticmethod
    def voice_id() -> str:
        return (config.get('tts.fish', {}) or {}).get('reference_id', '')

    def _get_ws_url(self) -> str:
        return "wss://api.fish.audio/v1/tts/live"

    def _get_ws_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "model": self.model_id,
        }

    def _send_event(self, event: dict):
        """The Fish Audio live protocol is MessagePack over binary WS frames."""
        self.ws.send(ormsgpack.packb(event), opcode=ABNF.OPCODE_BINARY)

    def _build_start_event(self) -> dict:
        request = {
            "text": "",
            "format": "pcm",
            "sample_rate": self.sample_rate,
            "chunk_length": self.chunk_length,
            "latency": self.latency,
            "prosody": {"speed": self.speed},
        }
        if self.voice_id():
            request["reference_id"] = self.voice_id()
        return {"event": "start", "request": request}

    def start(self, llm_text_stream: Iterator[str]) -> Iterator[bytes]:
        """Set up the audio stream with text chunks from the LLM."""
        self.llm_text_stream = llm_text_stream
        self._clear_queue(self.audio_queue)

        if not self.is_connected:
            self.connect()

        threading.Thread(target=self._feed_text_to_queue, daemon=True).start()
        self.audio_stream = self._audio_generator()
        return self.audio_stream

    def _on_open(self, ws):
        """Handle WebSocket connection open"""
        self.is_connected = True
        info(f"Fish Audio session starting (model={self.model_id}, latency={self.latency})")
        self._send_event(self._build_start_event())
        threading.Thread(target=self._feed_text_thread, daemon=True).start()

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            if not isinstance(message, (bytes, bytearray)):
                trace(f"Ignoring non-binary Fish Audio frame: {str(message)[:80]}")
                return

            data = ormsgpack.unpackb(bytes(message))
            event = data.get("event")

            if event == "audio":
                audio = data.get("audio")
                if audio:
                    self.audio_queue.put(bytes(audio))
            elif event == "finish":
                if data.get("reason") == "error":
                    error(f"Fish Audio finished with error: {data}")
                else:
                    import time
                    time.sleep(1.0)
                self.audio_queue.put(None)
            elif event == "log":
                trace(f"Fish Audio log: {data.get('message')}")
        except Exception as e:
            error(f"Error processing Fish Audio message: {e}")

    def _on_error(self, ws, e):
        """Handle WebSocket errors"""
        error_msg = str(e)
        if "Connection is already closed" not in error_msg and "object has no attribute 'sock'" not in error_msg:
            error(f"Fish Audio WebSocket error: {e}")
        self.audio_queue.put(None)

    def _feed_text_thread(self):
        """Thread that feeds text to the WebSocket"""
        try:
            while not self.is_closed:
                try:
                    text_chunk = self.text_queue.get(timeout=0.5)

                    if text_chunk is None:
                        # Server synthesizes remaining buffered text, then sends finish.
                        self._send_event({"event": "stop"})
                        break

                    if not text_chunk.strip():
                        self.text_queue.task_done()
                        continue

                    self._send_event({"event": "text", "text": text_chunk})
                    self.text_queue.task_done()

                except queue.Empty:
                    continue
        except Exception as e:
            if "Connection is already closed" not in str(e):
                error(f"Error in Fish Audio feed text thread: {e}")
            # The finish event will never arrive — unblock the request thread.
            self.audio_queue.put(None)
        finally:
            self.text_queue.put(None)
