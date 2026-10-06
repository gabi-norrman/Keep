from assistant.commands.CommandSet import CommandSet
from integrations.weather.WeatherIntegration import WeatherIntegration


class WeatherCommands(CommandSet):
    """The detailed weather forecast; documented in the prompt itself, outside any toolkit."""

    def cmd_weather(self, parameter_string: str):
        self.background_command(WeatherIntegration.fetch_weather_forecast, False)

    def describe_weather(self, p):                return "Checking the weather"
