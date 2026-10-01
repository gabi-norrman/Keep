from ..PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace

NOTHING_REMEMBERED = "Nothing since the last curation."


class RecentMemorySectionGenerator(PromptSectionGenerator):
    """Shows what `remember` added to the memory bank since its last curation, apart from the bank itself.

    The bank sits near the top of the prompt, ahead of the static command reference; a note
    appended there changed the cached prefix from that point on, and every request after the
    next rebuild re-wrote the whole prompt cache. Rendered down here, in the part that is
    rebuilt hourly anyway, a new note leaves the top untouched. It has no thread of its own:
    MemorySectionGenerator reads the bank once and hands this section its share (`offer`),
    then `publish`es it — so the two halves always come from the same read of the file.
    """

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "RememberedNotes")
        self.content = NOTHING_REMEMBERED
        self._published = self.content

    def offer(self, appended: str):
        self.content = appended.strip() or NOTHING_REMEMBERED

    def publish(self):
        if self.content == self._published:
            return
        trace(f"Remembered notes changed ({len(self._published)} → {len(self.content)} chars)")
        self._published = self.content
        self.notify_update()

    def _start_monitoring(self):
        self._stop_event.wait()

    def _fetch_content(self) -> str:
        return self.content
