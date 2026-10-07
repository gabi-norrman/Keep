from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from integrations.weather.WeatherIntegration import WeatherIntegration


class PrecipitationSectionGenerator(PromptSectionGenerator):
    """Refreshes the short-term precipitation forecast section every 5 minutes."""
    key = "precipitation"
    heading = "## Short term precipitation"
    intro = ("Based on rain radar projections and very accurate — THIS is the most accurate rain reading we have. "
             "Values are mm/h, averaged over each labelled time window:")

    UPDATE_INTERVAL = 5 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Precipitation")

    def _fetch_content(self) -> str:
        """Fetch short-term precipitation data."""
        return WeatherIntegration.fetch_short_term_precipitation()
