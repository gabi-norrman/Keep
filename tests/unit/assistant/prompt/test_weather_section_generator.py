from unittest.mock import patch


class TestWeatherSectionGenerator:
    @patch('assistant.prompt.sections.WeatherSectionGenerator.WeatherIntegration')
    def test_fetch_content_calls_weather(self, mock_weather):
        mock_weather.fetch_weather_forecast.return_value = "Sunny 25C"
        from assistant.prompt.sections.WeatherSectionGenerator import WeatherSectionGenerator
        section = WeatherSectionGenerator(None)
        result = section._fetch_content()
        mock_weather.fetch_weather_forecast.assert_called_once_with(True)
        assert result == "Sunny 25C"
