import os
from ..PromptSectionGenerator import PromptSectionGenerator
from .RecentMemorySectionGenerator import RecentMemorySectionGenerator
from assistant import FirstRun, MemoryFile
from common.logging import trace, info

FIRST_CURATION_NOTICE = ("[Heads up: your long-term memory is being built for the first time right now. "
                         "What follows is a work in progress and may be incomplete or wrong until that finishes.]")


class MemorySectionGenerator(PromptSectionGenerator):
    """Keeps the memory section in sync with the memory bank by reloading it as soon as the file changes.
    The section is the curated bank only; the notes `remember` appended since the last curation
    go to `self.recent`, a section of its own further down the prompt (see RecentMemorySectionGenerator).
    While the first-run pipeline curates the first bank (`FirstRun.curation_progress`) the section
    opens with `FIRST_CURATION_NOTICE`, the prompt's counterpart of the Memory tab's heads-up band,
    and refreshes when that state flips — the curation's last chunk writes the bank before the
    progress clears, so the mtime alone would leave the notice in place until the next edit."""

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Memory")
        self.file_path = MemoryFile.MEMORY_FILE
        self.last_mtime = 0
        self._curating = False
        self.recent = RecentMemorySectionGenerator(prompt_manager)

    def _refresh(self):
        """Update both halves from one read: the bank first, so a rebuild it triggers already
        carries the matching notes, then the notes for the case where only they changed."""
        self._update_content()
        self.recent.publish()

    def _start_monitoring(self):
        """Check file modification time every second."""
        while not self._stop_event.is_set():
            curating = self._first_curation_running()
            if curating != self._curating:
                info(f"Memory section: first curation {'started' if curating else 'finished'} — "
                     f"{'adding' if curating else 'dropping'} the heads-up notice")
                self._curating = curating
                self._refresh()
            if os.path.exists(self.file_path):
                try:
                    current_mtime = os.path.getmtime(self.file_path)
                    if current_mtime > self.last_mtime:
                        self._refresh()
                        self.last_mtime = current_mtime
                except Exception as e:
                    trace(f"Error checking memory file mtime: {e}")
            self._wait(1)

    @staticmethod
    def _first_curation_running() -> bool:
        return FirstRun.curation_progress() is not None

    def _fetch_content(self) -> str:
        """Read the curated bank, under the first-curation notice while that runs, and hand
        the notes appended since to `self.recent`."""
        memory, appended = MemoryFile.split(self.file_path)
        info(f"Fetching memory: {len(memory)} chars curated, {len(appended.strip())} chars remembered since")
        self.recent.offer(appended)
        if self._first_curation_running():
            return f"{FIRST_CURATION_NOTICE}\n\n{memory}" if memory else FIRST_CURATION_NOTICE
        return memory
