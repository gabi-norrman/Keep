import base64
import json
import queue
import threading
from typing import Iterator, List

import requests as _requests

from .WebSocketVoiceSynthesizer import WebSocketVoiceSynthesizer
from assistant.integration.Integration import Integration
from common.logging import error
from assistant.prompt.TimeOfDay import TimeOfDay
from common.ConfigManager import config


class ElevenLabsVoiceSynthesizer(WebSocketVoiceSynthesizer, Integration):
    """Streams speech from the ElevenLabs websocket TTS API."""

    CONFIG_SECTION = "elevenlabs"

    # The engine every non-Swedish reply speaks through, so a voice install needs only its
    # key: on without an enabled_integrations entry, listed as "not configured" until then.
    enabled_by_default = True

    @property
    def name(self) -> str:
        return "ElevenLabs TTS"

    @property
    def config_keys(self) -> List[str]:
        return ["api_keys.elevenlabs"]

    def check_health(self) -> bool:
        return _requests.get("https://api.elevenlabs.io/v1/user",
                             headers={"xi-api-key": self.api_key}, timeout=10).status_code == 200

    def __init__(self):
        WebSocketVoiceSynthesizer.__init__(self)
        Integration.__init__(self)
        self.api_key: str = config.get_api_key('elevenlabs')
        self.model_id: str = "eleven_flash_v2_5"
        self.optimize_streaming_latency: int = 4  # this seems to win around 100ms, haven't made any proper analysis though, just gut feeling
        self.output_format: str = "pcm_24000"

    @staticmethod
    def voice_id() -> str:
        if TimeOfDay.get_current() == TimeOfDay.DAY:
            return WebSocketVoiceSynthesizer.configured_voice("elevenlabs", "QtY3JBOUKEB5xzrRfOKc")  # Maisie, normal speech
        # Night, morning and late evening we whisper
        return WebSocketVoiceSynthesizer.configured_voice("elevenlabs", "geVuXdUpU0lgUukWJCUE", key="night_voice")  # Shannon, ASMR American whisper

    def _get_ws_url(self) -> str:
        return f"wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id()}/stream-input?model_id={self.model_id}&optimize_streaming_latency={self.optimize_streaming_latency}&output_format={self.output_format}"

    def _get_ws_headers(self) -> dict:
        return {"xi-api-key": self.api_key}

    def start(self, llm_text_stream: Iterator[str]) -> Iterator[bytes]:
        """Set up the audio stream with text chunks from the LLM, following original API.
        This takes a few 100ms to do, but we're waiting for the LLM anyway."""
        self.llm_text_stream = llm_text_stream
        self._clear_queue(self.audio_queue)

        if not self.is_connected:
            self.connect()

        threading.Thread(target=self._feed_text_to_queue, daemon=True).start()
        self.audio_stream = self._audio_generator()
        return self.audio_stream

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)

            if "audio" in data and data["audio"] is not None:
                audio_bytes = base64.b64decode(data["audio"])
                self.audio_queue.put(audio_bytes)

            if data.get("isFinal", False):
                import time
                time.sleep(1.0)
                self.audio_queue.put(None)
        except Exception as e:
            error(f"Error processing message: {e}")

    def _on_error(self, ws, e):
        """Handle WebSocket errors"""
        error_msg = str(e)
        if "Connection is already closed" not in error_msg and "'NoneType' object has no attribute 'sock'" not in error_msg:
            error(f"WebSocket error: {e}")
        self.audio_queue.put(None)

    def _on_open(self, ws):
        """Handle WebSocket connection open"""
        self.is_connected = True

        ws.send(json.dumps({
            "text": " ",
            "voice_settings": {
                "stability": 0.3,
                "similarity_boost": 0.5
            },
            "generation_config": {
                "chunk_length_schedule": [50, 50, 50, 50],
                "auto_mode": True,
            },
            "xi_api_key": self.api_key,
            "bos": True
        }))

        threading.Thread(target=self._feed_text_thread, daemon=True).start()

    def _feed_text_thread(self):
        """Thread that feeds text to the WebSocket"""
        try:
            while not self.is_closed:
                try:
                    text_chunk = self.text_queue.get(timeout=0.5)

                    if text_chunk is None:
                        self.ws.send(json.dumps({
                            "text": "",
                            "eos": True
                        }))
                        break

                    if not text_chunk.strip():
                        self.text_queue.task_done()
                        continue

                    self.ws.send(json.dumps({
                        "text": text_chunk,
                        "try_trigger_generation": True,
                        "bos": False
                    }))

                    self.text_queue.task_done()

                except queue.Empty:
                    continue
        except Exception as e:
            if "Connection is already closed" not in str(e):
                error(f"Error in feed text thread: {e}")
        finally:
            self.text_queue.put(None)
