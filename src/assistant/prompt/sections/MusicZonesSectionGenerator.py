import threading

from assistant.integration.music import MusicSystem as music
from common.logging import info

from ..PromptSectionGenerator import PromptSectionGenerator


class MusicZonesSectionGenerator(PromptSectionGenerator):
    """Keeps the zones the music toolkit carries fresh — every speaker system's, with what each plays — as their changes are reported or, for a system that cannot report them, every 3 seconds.

    A system that pushes its changes (`pushes_changes()`, HEOS while its event stream is up) wakes
    the section through `zones_changed()`, so a change reaches the LLM at once; while every active
    system pushes, the section looks again only every `PUSHED_INTERVAL` seconds besides, a safety
    net. While any does not (Sonos), it polls every `UPDATE_INTERVAL` seconds as well. The base loop
    skips downstream work when the status is byte-identical to the current content, and the status
    carries no per-fetch field (no elapsed time or position). It runs only while a MusicSystem
    integration is on, so a house without speakers never looks for one."""
    key = "music_zones"
    needs = music.MusicSystem

    UPDATE_INTERVAL = 3
    PUSHED_INTERVAL = 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "MusicZones")
        self._wake = threading.Event()   # set by a reported change or stop(): ends the current wait early
        self._on_zones_changed = self._wake.set

    def stop(self):
        self._stop_event.set()   # before the wake, so the wait it ends reports the stop
        self._wake.set()
        super().stop()

    def _wait(self, seconds: float) -> bool:
        """The base wait, cut short by a reported zone change as well as by stop()."""
        self._wake.wait(seconds)
        self._wake.clear()
        return self._stop_event.is_set()

    def _interval(self) -> float:
        systems = music.active()
        if systems and all(system.pushes_changes() for system in systems):
            return self.PUSHED_INTERVAL
        return self.UPDATE_INTERVAL

    def _start_monitoring(self):
        music.add_zone_listener(self._on_zones_changed)
        try:
            while not self._stop_event.is_set():
                if self._wait(self._interval()):
                    break
                self._update_content()
        finally:
            music.remove_zone_listener(self._on_zones_changed)

    def _fetch_content(self) -> str:
        house = music.speakers()
        if not house.systems:
            info("MusicZones: no speaker system ready — section empty until one is")
            return ""
        return house.zone_status()
