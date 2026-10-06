from unittest.mock import Mock, patch

import pytest

from integrations.tesla.TeslaIntegration import TeslaCommandError, TeslaIntegration


@pytest.fixture
def tesla_api():
    with patch.object(TeslaIntegration, "get_instance", return_value=Mock()) as get_instance:
        yield get_instance.return_value


class TestTeslaBackgroundCommands:
    def test_defrost_success_is_silent(self, commands_instance, tesla_api):
        mock_api = tesla_api
        mock_api.set_max_defrost.return_value = "Max defrost enabled for Model Y."

        commands_instance.run("tesla_max_defrost", ["Model Y"])
        commands_instance.wait_for_background(timeout=5)

        mock_api.set_max_defrost.assert_called_once_with("Model Y")
        commands_instance.command_processor.set_command_reply.assert_not_called()
        commands_instance.command_processor.background_command_done.assert_called_once()

    def test_error_caught_and_returned(self, commands_instance, tesla_api):
        mock_api = tesla_api
        mock_api.set_max_defrost.side_effect = TeslaCommandError("vehicle offline")

        commands_instance.run("tesla_max_defrost", [""])
        commands_instance.wait_for_background(timeout=5)

        commands_instance.command_processor.set_command_reply.assert_called_once_with(
            "Tesla command failed: vehicle offline", contains_error=False
        )

    def test_empty_param_passes_none(self, commands_instance, tesla_api):
        mock_api = tesla_api
        mock_api.start_air_conditioning.return_value = "Air conditioning started for car."

        commands_instance.run("tesla_air_con_on", [])
        commands_instance.wait_for_background(timeout=5)

        mock_api.start_air_conditioning.assert_called_once_with(None)
