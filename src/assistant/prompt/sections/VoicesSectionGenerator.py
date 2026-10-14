from ..PromptSectionGenerator import PromptSectionGenerator
from assistant import VoiceCandidates
from common.logging import info, trace


class VoicesSectionGenerator(PromptSectionGenerator):
    """Renders the voices the house keeps hearing that have no name yet, and re-renders as soon
    as the discovery files or refreshes one or the owner's answer ends one. Runs only while the
    voice interface is on (the discovery is its nightly job), and stands under its heading in
    the data block only while a voice is open — a house with nothing to ask about carries no
    VOICES section."""
    key = "voices"
    heading = "# VOICES"
    intro = ("Voices the house keeps hearing that have no name yet. The owner is asked who each one is, and a "
             "spoken request headed by one of these ids is that voice speaking — then ask the speaker. A name, "
             "or a reason to ignore the voice, goes through the `voices` toolkit, nowhere else:")
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
