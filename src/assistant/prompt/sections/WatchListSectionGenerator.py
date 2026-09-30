from ..PromptSectionGenerator import PromptSectionGenerator
from assistant import WatchList
from common.logging import info, trace


class WatchListSectionGenerator(PromptSectionGenerator):
    """Renders the open watch-list items and re-renders as soon as one is added, updated or
    closed — and at midnight, since an item's staleness is counted in days."""

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "WatchList")
        self.last_version = None

    def _start_monitoring(self):
        """Check the watch list's version every second."""
        while not self._stop_event.is_set():
            try:
                version = WatchList.version()
                if version != self.last_version:
                    self._update_content()
                    self.last_version = version
            except Exception as e:
                trace(f"Error checking the watch list: {e}")
            self._wait(1)

    def _fetch_content(self) -> str:
        content = WatchList.render()
        info(f"Watch list section: {len(WatchList.items())} open item(s)")
        return content
