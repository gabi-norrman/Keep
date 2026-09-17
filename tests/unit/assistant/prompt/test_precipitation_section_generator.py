from unittest.mock import patch


class TestPrecipitationSectionGenerator:
    @patch('assistant.prompt.sections.PrecipitationSectionGenerator.WeatherIntegration')
    def test_calls_short_term_precipitation(self, mock_weather):
        mock_weather.fetch_short_term_precipitation.return_value = "No rain expected"
        from assistant.prompt.sections.PrecipitationSectionGenerator import PrecipitationSectionGenerator
        section = PrecipitationSectionGenerator(None)
        result = section._fetch_content()
        mock_weather.fetch_short_term_precipitation.assert_called_once()
        assert result == "No rain expected"
