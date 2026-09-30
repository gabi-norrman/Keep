from ..PromptSectionGenerator import PromptSectionGenerator
from assistant import Procedures
from common.logging import info, trace


class ProceduresSectionGenerator(PromptSectionGenerator):
    """Renders the index of the saved procedures — one line per file, read from the files —
    and re-renders as soon as one is saved, annotated or deleted."""

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Procedures")
        self.last_version = None

    def _start_monitoring(self):
        """Check the procedures' version every second."""
        while not self._stop_event.is_set():
            try:
                version = Procedures.version()
                if version != self.last_version:
                    self._update_content()
                    self.last_version = version
            except Exception as e:
                trace(f"Error checking the procedures: {e}")
            self._wait(1)

    def _fetch_content(self) -> str:
        info(f"Procedures section: {len(Procedures.names())} procedure(s)")
        return Procedures.render_index()
