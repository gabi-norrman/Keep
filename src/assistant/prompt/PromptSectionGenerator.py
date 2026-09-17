import threading
import time
from abc import ABC, abstractmethod
from common.logging import error, trace, info


class PromptSectionGenerator(ABC):
    """Fetches one dynamic prompt section on its own thread and notifies PromptManager when it changes.

    Subclasses implement _fetch_content() and either set UPDATE_INTERVAL for plain polling or
    override _start_monitoring() for a custom update strategy. Failed refreshes keep the last
    good content marked stale rather than blanking the section.
    """

    def __init__(self, prompt_manager, section_name: str):
        self.prompt_manager = prompt_manager
        self.section_name = section_name
        self.content = ""
        self.last_update_time = 0
        self._refresh_failed_at = None
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            trace(f"Section '{self.section_name}' already started.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._start_monitoring_wrapper, name=f"prompt-{self.section_name}")
        self._thread.daemon = True
        self._thread.start()

    def _start_monitoring_wrapper(self):
        while not self._stop_event.is_set():
            try:
                self._start_monitoring()
                break
            except Exception as e:
                msg = str(e).replace("\n", "")
                info(f"Section '{self.section_name}' failed: {msg}. Retrying in 5 seconds...")
                self._wait(5)
                if not self._stop_event.is_set():
                    trace(f"Restarting monitoring for section '{self.section_name}'...")

    def stop(self):
        trace(f"Stopping section generator: {self.section_name}")
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _wait(self, seconds: float) -> bool:
        """Sleep for `seconds` unless stop() is called first; True when it was. Monitoring loops
        wait with this, not time.sleep, so a stop() never has to wait out a poll interval."""
        return self._stop_event.wait(seconds)

    def get_content(self) -> str:
        """Get the current content of this section. This needs to be instant."""
        return self.content

    def _update_content(self):
        """
        Internal method to update content with error handling and retry logic.
        On failure after 3 retries, keeps the last good content marked as stale
        (or sets ERROR content if there is nothing to keep).
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                new_content = self._fetch_content()
                self._refresh_failed_at = None
                if new_content == self.content:
                    # skip if content unchanged
                    return
                self.content = new_content
                self.last_update_time = time.time()
                self.notify_update()
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    info(f"Failed to update section '{self.section_name}' (attempt {attempt + 1}/{max_retries}): {e}. Retrying in 1 second...")
                    time.sleep(1)
                else:
                    self._handle_fetch_failure(e, max_retries)

    def _handle_fetch_failure(self, e: Exception, max_retries: int):
        """Wiping good content over a transient outage would blind the LLM, so keep it and mark it stale instead."""
        if self.content and not self.content.startswith("ERROR:"):
            if self._refresh_failed_at is not None:
                error(f"Failed to update section '{self.section_name}' after {max_retries} attempts: {e}. Content already marked stale since {self._refresh_failed_at}.")
                return
            self._refresh_failed_at = time.strftime("%H:%M")
            error(f"Failed to update section '{self.section_name}' after {max_retries} attempts: {e}. Keeping last good content, marked stale.")
            self.content += f"\n[WARNING: refresh failed at {self._refresh_failed_at}, data above may be stale]"
        else:
            error(f"Failed to update section '{self.section_name}' after {max_retries} attempts: {e}. Setting ERROR content.")
            self.content = f"ERROR: Could not fetch section '{self.section_name}': {e}"
        self.last_update_time = time.time()
        self.notify_update()

    def notify_update(self):
        """Notify PromptManager that this section has been updated."""
        if self.prompt_manager:
            self.prompt_manager.on_section_updated(self.section_name)

    UPDATE_INTERVAL = None

    def _start_monitoring(self):
        """
        Start monitoring for updates. This method runs in a separate thread.
        Default implementation polls at UPDATE_INTERVAL seconds.
        Subclasses with non-standard strategies should override this.

        Must respect self._stop_event for graceful shutdown.
        """
        if self.UPDATE_INTERVAL is None:
            raise NotImplementedError("Subclass must set UPDATE_INTERVAL or override _start_monitoring")
        self._update_content()
        while not self._stop_event.is_set():
            self._wait(self.UPDATE_INTERVAL)
            if not self._stop_event.is_set():
                self._update_content()

    @abstractmethod
    def _fetch_content(self) -> str:
        """
        Fetch the actual content for this section.
        This method should return a string containing the section content.

        Returns:
            str: The content for this prompt section
        """
        pass
