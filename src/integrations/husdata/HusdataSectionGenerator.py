from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from integrations.husdata.HusdataIntegration import HusdataIntegration


class HusdataSectionGenerator(PromptSectionGenerator):
    """Refreshes the heat pump's readings every minute from the Husdata gateway."""
    key = "heat_pump"
    heading = "# HEAT PUMP"
    intro = ("The heat pump's readings from its Husdata gateway, refreshed every minute. The settings you can "
             "change are in the husdata toolkit (`load_toolkit(\"husdata\")`).")

    UPDATE_INTERVAL = 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "HeatPump")

    def _fetch_content(self) -> str:
        return HusdataIntegration.get_instance().snapshot()
