from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import info
from integrations.music.MusicIntegration import MusicManager


class MusicZonesSectionGenerator(PromptSectionGenerator):
    """Keeps the Sonos zones status section of the prompt fresh by polling every 3 seconds.

    Polls every 3 seconds. The base loop skips downstream work when the fetched
    status is byte-identical to the current content, and the status string carries
    no per-fetch field (no elapsed time / position), so a real change — including
    one made externally in the Sonos app — is reflected in the prompt within 3s
    while a steady state costs only a cheap UPnP round-trip.

    It reads the integration the IntegrationMonitor built and never builds one; it runs only
    while Sonos & Spotify is on, so a house without it never looks for a speaker.
    """
    key = "music_zones"
    heading = "## Sonos zones"
    intro = ("The zones music can play in. If no zone/room is specified assume the user means the Kitchen, unless "
             "previous context suggests otherwise:")

    UPDATE_INTERVAL = 3

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "MusicZones")

    def _fetch_content(self) -> str:
        manager = MusicManager.peek_instance()
        if manager is None:
            info("MusicZones: Sonos & Spotify is being rebuilt — section empty until it is back")
            return ""
        return manager.get_all_zones_status()
