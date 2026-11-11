from assistant.integration.music import MusicSystem as music
from common.logging import info

from ..PromptSectionGenerator import PromptSectionGenerator


class MusicZonesSectionGenerator(PromptSectionGenerator):
    """Keeps the zones the music toolkit carries fresh — every speaker system's, with what each plays — polling every 3 seconds.

    The base loop skips downstream work when the fetched status is byte-identical to the current
    content, and the status carries no per-fetch field (no elapsed time or position), so a real
    change — one made in a speaker system's own app included — reaches the LLM within 3 s while a
    steady state costs one cheap round-trip per system. It runs only while a MusicSystem
    integration is on, so a house without speakers never looks for one."""
    key = "music_zones"
    needs = music.MusicSystem

    UPDATE_INTERVAL = 3

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "MusicZones")

    def _fetch_content(self) -> str:
        house = music.speakers()
        if not house.systems:
            info("MusicZones: no speaker system ready — section empty until one is")
            return ""
        return house.zone_status()
