"""The HOME AUTOMATION DATA section the `home` toolkit carries: whatever backend
HomeAutomation.active() resolves to (one, or the composite over several), polled for a
snapshot every `state_poll_interval` seconds, pruned by the backend's filters and rendered as
the `## LIGHTS` / `## SENSORS` / `## VARIABLES` / `## ACTIONS` blocks. Without a backend the
section is the setup hint; while no backend is registered yet (the sections start before the
launcher reaches the feeds) it is the "still starting" hint and the poll runs at the backend's
short startup interval."""
from typing import Optional

from common.ConfigManager import config
from common.logging import info, trace
from assistant.integration.HomeAutomation import HomeAutomation, HomeSnapshot, NotConfiguredHome, active, render

from ..PromptSectionGenerator import PromptSectionGenerator

DEFAULT_NIGHT_VARIABLE = "Night"


class HomeSectionGenerator(PromptSectionGenerator):
    """Refreshes the home automation state section from the active backend; runs only while a
    home automation integration is on."""
    needs = HomeAutomation

    UPDATE_INTERVAL = 30

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "HomeData")
        self._latest: Optional[HomeSnapshot] = None
        self._poll_interval: float = HomeAutomation.state_poll_interval

    def _start_monitoring(self):
        self._update_content()
        while not self._stop_event.is_set():
            self._wait(self._poll_interval)
            if not self._stop_event.is_set():
                self._update_content()

    def _fetch_content(self) -> str:
        backend = active()
        self._poll_interval = backend.state_poll_interval
        if isinstance(backend, NotConfiguredHome):
            self._latest = None
            return f"({backend.hint})"
        snapshot = backend.snapshot()
        self._latest = snapshot
        if snapshot.is_empty():
            info(f"{backend.name}: the snapshot is empty — nothing to show the prompt")
        return render(backend.prune(snapshot))

    def is_night(self) -> bool:
        """Whether the house's `home.night_variable` is set, from the live snapshot — not the assembled
        prompt, which a small section delta leaves frozen for up to the rebuild interval while
        the autonomous night guards need the flip at once."""
        if self._latest is None:
            trace("Night check: no home automation snapshot yet — assuming not Night")
            return False
        return self._latest.flag(config.get('home.night_variable', DEFAULT_NIGHT_VARIABLE)) is True
