import base64
import json
import queue
import threading
import time
from typing import Iterator, List

from .WebSocketVoiceSynthesizer import WebSocketVoiceSynthesizer
from assistant.integration.Integration import Integration
from common.logging import error, info, trace
from common.ConfigManager import config


class GrokVoiceSynthesizer(WebSocketVoiceSynthesizer, Integration):
    """A voice from xAI's Grok TTS, streamed as the answer is written.

    Speaks Keep's replies with xAI's Grok TTS, streamed while the answer is still being written.

    Streams over the websocket API."""

    CONFIG_SECTION = "grok"

    @property
    def name(self) -> str:
        return "Grok TTS"

    @property
    def config_keys(self) -> List[str]:
        return ["api_keys.grok"]

    def check_health(self) -> bool:
        # Health check disabled: x.ai's /v1/tts/voices endpoint returns spurious
        # empty-body HTTP 500s often enough that the probe was more noise than signal
        # and kept flapping Grok TTS into WARNING/ERROR despite a valid key. Real TTS
        # failures still surface via the WebSocket path at synthesis time.
        trace("Grok TTS health check is disabled, reporting healthy")
        return True

    def __init__(self):
        WebSocketVoiceSynthesizer.__init__(self)
        Integration.__init__(self)
        self.api_key: str = config.get_api_key('grok')
        self.sample_rate: int = 24000

    @staticmethod
    def voice_id() -> str:
        return WebSocketVoiceSynthesizer.configured_voice("grok", "ara")

    def _get_ws_url(self) -> str:
        return f"wss://api.x.ai/v1/tts?language={self.configured_language_code()}&voice={self.voice_id()}&codec=pcm&sample_rate={self.sample_rate}"

    def _get_ws_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def start(self, llm_text_stream: Iterator[str]) -> Iterator[bytes]:
        self.llm_text_stream = llm_text_stream
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

    def _on_open(self, ws):
        self.is_connected = True
        info("Grok TTS WebSocket connected")
        threading.Thread(target=self._feed_text_thread, daemon=True).start()

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "audio.delta":
                audio_data = data.get("delta")
                if audio_data:
                    audio_bytes = base64.b64decode(audio_data)
                    self.audio_queue.put(audio_bytes)

            elif msg_type == "audio.done":
                time.sleep(1.0)
                self.audio_queue.put(None)

            elif msg_type == "error":
                error(f"Grok TTS error: {data.get('message', 'Unknown error')}")
                self.audio_queue.put(None)

        except Exception as e:
            error(f"Error processing Grok TTS message: {e}")

    def _on_error(self, ws, e):
        error_msg = str(e)
        if "Connection to remote host was lost" not in error_msg and "object has no attribute 'sock'" not in error_msg:
            info(f"Grok TTS WebSocket error: {e}")
        self.audio_queue.put(None)

    def _feed_text_thread(self):
        try:
            buffered_text = ""

            while not self.is_closed:
                try:
                    text_chunk = self.text_queue.get(timeout=0.5)

                    if text_chunk is None:
                        if buffered_text.strip():
                            self.ws.send(json.dumps({"type": "text.delta", "delta": buffered_text}))
                        self.ws.send(json.dumps({"type": "text.done"}))
                        info("Grok TTS text stream complete")
                        break

                    if not text_chunk.strip():
                        self.text_queue.task_done()
                        continue

                    buffered_text += text_chunk

                    last_open = buffered_text.rfind('<')
                    last_close = buffered_text.rfind('>')
                    has_incomplete_tag = last_open > last_close

                    if has_incomplete_tag:
                        self.text_queue.task_done()
                        continue

                    if buffered_text.strip():
                        self.ws.send(json.dumps({"type": "text.delta", "delta": buffered_text}))

                    buffered_text = ""
                    self.text_queue.task_done()

                except queue.Empty:
                    continue
        except Exception as e:
            if "Connection is already closed" not in str(e):
                error(f"Error in Grok TTS feed text thread: {e}")
        finally:
            self.text_queue.put(None)
