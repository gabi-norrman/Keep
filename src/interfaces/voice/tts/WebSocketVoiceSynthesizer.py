import threading
from abc import abstractmethod

from .VoiceSynthesizer import VoiceSynthesizer


class WebSocketVoiceSynthesizer(VoiceSynthesizer):
    """Base for TTS backends that stream text in and audio out over a single websocket."""

    def __init__(self):
        super().__init__()
        self.ws = None
        self.is_connected = False
        self.ws_thread = None

    @abstractmethod
    def _get_ws_url(self) -> str: ...

    @abstractmethod
    def _get_ws_headers(self) -> dict: ...

    def connect(self):
        if self.ws_thread and self.ws_thread.is_alive():
            return

        self.is_closed = False
        self.ws_thread = threading.Thread(target=self._run_websocket, daemon=True)
        self.ws_thread.start()

    def _run_websocket(self):
        import websocket
        import ssl

        ssl_opt = {"cert_reqs": ssl.CERT_NONE}

        self.ws = websocket.WebSocketApp(
            self._get_ws_url(),
            header=self._get_ws_headers(),
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )

        self.ws.run_forever(sslopt=ssl_opt)

    def _on_close(self, ws, close_status_code, close_msg):
        self.is_connected = False
        self.audio_queue.put(None)

    def _cleanup(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def _join_threads(self):
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=2)
