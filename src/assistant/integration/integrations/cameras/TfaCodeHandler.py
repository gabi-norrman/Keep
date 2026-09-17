import threading
import time
from typing import Optional
from assistant.SystemNote import system_note

from common.logging import info


class TfaCodeHandler:
    """Blocks until a 2FA code is provided via auto-detection or the Floxy TUI.

    Shared base for the camera integrations (Ring, Arlo). Subclasses set
    ``service_name`` / ``detection_source`` for logging and implement
    ``_notification_message``; Arlo layers email polling on top.
    """

    service_name: str = ""
    detection_source: str = "auto-detection + TUI"

    def __init__(self):
        self._code: Optional[str] = None
        self._event = threading.Event()
        self._waiting = False
        self._waiting_since: Optional[float] = None

    def start(self):
        self._code = None
        self._event.clear()
        self._waiting = True
        self._waiting_since = time.time()

    def get(self, timeout: float = 300) -> Optional[str]:
        info(f"{self.service_name}: Waiting for 2FA code ({self.detection_source})...")
        try:
            self._event.wait(timeout=timeout)
            return self._code
        finally:
            self._waiting = False

    def submit_code(self, code: str):
        info(f"{self.service_name}: 2FA code submitted.")
        self._code = code
        self._waiting = False
        self._event.set()

    @property
    def is_waiting(self) -> bool:
        """True only while a 2FA flow started via ``start()`` is blocked in ``get()``."""
        return self._waiting

    @property
    def waiting_since(self) -> Optional[float]:
        """Unix time of the most recent ``start()`` call, for staleness checks."""
        return self._waiting_since

    def _notify_2fa_needed(self):
        """A system row: the user sees it in every chat and the LLM knows a code is being
        fetched, so it does not send the user to the TUI tab for nothing."""
        system_note(self.service_name, self._notification_message())

    def _notification_message(self) -> str:
        raise NotImplementedError
