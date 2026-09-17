from ..PromptSectionGenerator import PromptSectionGenerator
from ...integration.integrations.MusicIntegration import MusicManager


class MusicZonesSectionGenerator(PromptSectionGenerator):
    """Keeps the Sonos zones status section of the prompt fresh by polling every 3 seconds.

    Polls every 3 seconds. The base loop skips downstream work when the fetched
    status is byte-identical to the current content, and the status string carries
    no per-fetch field (no elapsed time / position), so a real change — including
    one made externally in the Sonos app — is reflected in the prompt within 3s
    while a steady state costs only a cheap UPnP round-trip.
    """

    UPDATE_INTERVAL = 3

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "MusicZones")
        self.playback_manager = MusicManager.get_instance()

    def _fetch_content(self) -> str:
        res = self.playback_manager.get_all_zones_status()
        return res
