from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.ConfigManager import config
from common.logging import info
from integrations.homeclaw.HomeClawClient import HomeClawUnavailable
from integrations.homeclaw.HomeClawIntegration import HomeClawIntegration

NOT_RUNNING = "HomeClaw is not running on the server's Mac, so there is no HomeKit data until it is started again."


class HomeClawSectionGenerator(PromptSectionGenerator):
    """Keeps the HomeKit section of the prompt fresh: the HomeClaw accessories by room, every minute.

    Reads the integration the IntegrationMonitor built and never builds one; while the app is
    not running the section says so instead of showing stale readings as current."""
    key = "homeclaw"
    heading = "# HOMEKIT (HOMECLAW)"
    intro = ("The HomeKit accessories Hue and Plejd do not already cover, read through the HomeClaw app and grouped "
             "by room; each line ends with its ref in brackets — pass that ref to the commands of the homeclaw "
             "toolkit (`load_toolkit(\"homeclaw\")`), since several accessories share a name.")

    UPDATE_INTERVAL = 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "HomeKit")
        self.UPDATE_INTERVAL = max(15, int(config.get("homeclaw.refresh_seconds", 60) or 60))

    def _fetch_content(self) -> str:
        homeclaw = HomeClawIntegration.peek_instance()
        if homeclaw is None:
            info("HomeKit: HomeClaw is being rebuilt — section empty until it is back")
            return ""
        try:
            return homeclaw.snapshot()
        except HomeClawUnavailable as e:
            info(f"HomeKit: {e}")
            return NOT_RUNNING
