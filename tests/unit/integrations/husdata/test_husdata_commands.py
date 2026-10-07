from unittest.mock import Mock, patch

import pytest

from integrations.husdata.HusdataCommands import HusdataCommands
from integrations.husdata.HusdataIntegration import HusdataError, HusdataIntegration


@pytest.fixture
def pump():
    with patch.object(HusdataIntegration, "get_instance", return_value=Mock()) as get_instance:
        yield get_instance.return_value


class TestSetCommands:
    def test_the_gateway_confirmation_is_the_reply(self, commands_instance, pump):
        pump.set_room_target.return_value = "The room target is now 21 °C"

        commands_instance.run("set_room_target", ["21"])
        commands_instance.wait_for_background(timeout=5)

        pump.set_room_target.assert_called_once_with(21.0)
        commands_instance.command_processor.set_command_reply.assert_called_once_with(
            "The room target is now 21 °C", contains_error=False)

    def test_a_decimal_comma_and_a_unit_are_accepted(self, commands_instance, pump):
        commands_instance.run("set_hot_water", ["52,5 °C"])
        commands_instance.wait_for_background(timeout=5)
        pump.set_hot_water.assert_called_once_with(52.5)

    def test_a_refusal_reaches_the_llm_as_text(self, commands_instance, pump):
        pump.set_hot_water.side_effect = HusdataError("This heat pump does not expose the hot water setpoint")

        commands_instance.run("set_hot_water", ["50"])
        commands_instance.wait_for_background(timeout=5)

        reply = commands_instance.command_processor.set_command_reply.call_args.args[0]
        assert "does not expose" in reply

    def test_a_non_number_never_reaches_the_pump(self, commands_instance, pump):
        commands_instance.run("set_room_target", ["warm"])
        commands_instance.wait_for_background(timeout=5)
        pump.set_room_target.assert_not_called()
        assert "warm" in commands_instance.command_processor.set_command_reply.call_args.args[0]


class TestFlags:
    def test_both_writes_need_approval(self):
        assert {"set_room_target", "set_hot_water"} <= HusdataCommands.SENSITIVE_COMMANDS
        assert set(HusdataCommands.APPROVAL_NOTES) == HusdataCommands.SENSITIVE_COMMANDS
