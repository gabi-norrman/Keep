from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from integrations.weather.WeatherIntegration import WeatherIntegration


class WeatherSectionGenerator(PromptSectionGenerator):
    """Refreshes the weather forecast the weather toolkit carries every 15 minutes."""
    key = "weather"

    UPDATE_INTERVAL = 15 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Weather")

    def _fetch_content(self) -> str:
        """Fetch weather forecast data."""
        return WeatherIntegration.fetch_weather_forecast(True)
