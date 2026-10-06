from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from integrations.weather.WeatherIntegration import WeatherIntegration


class PrecipitationSectionGenerator(PromptSectionGenerator):
    """Refreshes the short-term precipitation forecast section every 5 minutes."""
    key = "precipitation"

    UPDATE_INTERVAL = 5 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Precipitation")

    def _fetch_content(self) -> str:
        """Fetch short-term precipitation data."""
        return WeatherIntegration.fetch_short_term_precipitation()
