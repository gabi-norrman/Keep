from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import info
from integrations.aseko.AsekoIntegration import AsekoIntegration, render_units


class AsekoSectionGenerator(PromptSectionGenerator):
    """Keeps the pool section of the prompt fresh: the units' readings every 5 minutes.

    Reads the integration the IntegrationMonitor built and never builds one; it runs only
    while Aseko Pool is on, so a house without a pool never calls Aseko."""
    key = "pool"
    heading = "# POOL"
    intro = ("The pool's readings and its unit's own messages from Aseko Cloud, refreshed every 5 minutes. Read-only: "
             "nothing about the pool can be changed from here.")

    UPDATE_INTERVAL = 5 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Pool")

    def _fetch_content(self) -> str:
        api = AsekoIntegration.peek_instance()
        if api is None:
            info("Pool: Aseko Pool is being rebuilt — section empty until it is back")
            return ""
        return render_units(api.fetch_units(), api.held)
