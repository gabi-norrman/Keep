import os
from ..PromptSectionGenerator import PromptSectionGenerator
from assistant import FirstRun
from common.logging import trace, info

FIRST_CURATION_NOTICE = ("[Heads up: your long-term memory is being built for the first time right now. "
                         "What follows is a work in progress and may be incomplete or wrong until that finishes.]")


class MemorySectionGenerator(PromptSectionGenerator):
    """Keeps the memory section in sync with memory.md by reloading it as soon as the file changes.
    While the first-run pipeline curates the first bank (`FirstRun.curation_progress`) the section
    opens with `FIRST_CURATION_NOTICE`, the prompt's counterpart of the Memory tab's heads-up band,
    and refreshes when that state flips — the curation's last chunk writes memory.md before the
    progress clears, so the mtime alone would leave the notice in place until the next edit."""

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Memory")
        self.file_path = "memory.md"
        self.last_mtime = 0
        self._curating = False

    def _start_monitoring(self):
        """Check file modification time every second."""
        while not self._stop_event.is_set():
            curating = self._first_curation_running()
            if curating != self._curating:
                info(f"Memory section: first curation {'started' if curating else 'finished'} — "
                     f"{'adding' if curating else 'dropping'} the heads-up notice")
                self._curating = curating
                self._update_content()
            if os.path.exists(self.file_path):
                try:
                    current_mtime = os.path.getmtime(self.file_path)
                    if current_mtime > self.last_mtime:
                        self._update_content()
                        self.last_mtime = current_mtime
                except Exception as e:
                    trace(f"Error checking memory file mtime: {e}")
            self._wait(1)

    @staticmethod
    def _first_curation_running() -> bool:
        return FirstRun.curation_progress() is not None

    def _fetch_content(self) -> str:
        """Read memory.md, under the first-curation notice while that runs."""
        info("Fetching memory")
        memory = ""
        if os.path.exists(self.file_path):
            with open(self.file_path, "r") as file:
                memory = file.read()
        if self._first_curation_running():
            return f"{FIRST_CURATION_NOTICE}\n\n{memory}" if memory else FIRST_CURATION_NOTICE
        return memory
