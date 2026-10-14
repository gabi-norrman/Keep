from ..PromptSectionGenerator import PromptSectionGenerator
from assistant import VoiceCandidates
from common.logging import info, trace


class VoicesSectionGenerator(PromptSectionGenerator):
    """Renders the voices the house keeps hearing that have no name yet, and re-renders as soon
    as the discovery files or refreshes one or a name_voice ends one. The `voice`
    toolkit carries it (Toolkits.CARRIED_SECTIONS): the list goes out with the toolkit's
    reference on the voice channel, left out while no voice is open, and never stands in the
    prompt itself. Runs only while the voice interface is on, the discovery being its nightly
    job."""
    key = "voices"
    requires = "interfaces.voice.enabled"

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Voices")
        self.last_version = None

    def _start_monitoring(self):
        """Check the candidates' version every second."""
        while not self._stop_event.is_set():
            try:
                version = VoiceCandidates.version()
                if version != self.last_version:
                    self._update_content()
                    self.last_version = version
            except Exception as e:
                trace(f"Error checking the voice candidates: {e}")
            self._wait(1)

    def _fetch_content(self) -> str:
        content = VoiceCandidates.render_index()
        info(f"Voices section: {len(VoiceCandidates.items())} unnamed voice(s) listed in {len(content)} chars")
        return content
