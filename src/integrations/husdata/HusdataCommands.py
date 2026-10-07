from assistant.commands.CommandSet import CommandSet
from integrations.husdata.HusdataIntegration import HusdataError, HusdataIntegration


class HusdataCommands(CommandSet):
    """The heat pump's two settings: the room target and the hot water setpoint."""

    SENSITIVE_COMMANDS = frozenset({"set_room_target", "set_hot_water"})
    APPROVAL_NOTES = {
        "set_room_target": "Changes the room temperature the heat pump aims for, on the pump itself.",
        "set_hot_water": "Changes the hot water temperature the heat pump keeps, on the pump itself.",
    }

    @staticmethod
    def _celsius(parameter_string: str) -> float:
        try:
            return float(str(parameter_string).strip().replace(",", ".").removesuffix("°C").removesuffix("C").strip())
        except ValueError:
            raise HusdataError(f"Not a temperature: {parameter_string!r}")

    def _set(self, setter_name: str, parameter_string: str) -> str:
        try:
            setter = getattr(HusdataIntegration.get_instance(), setter_name)
            return setter(self._celsius(parameter_string))
        except HusdataError as e:
            return str(e)

    def cmd_set_room_target(self, parameter_string: str):
        self.background_command(lambda p: self._set("set_room_target", p), parameter_string)

    def cmd_set_hot_water(self, parameter_string: str):
        self.background_command(lambda p: self._set("set_hot_water", p), parameter_string)

    def describe_set_room_target(self, p):    return f"Setting the heat pump's room target to {p}"
    def describe_set_hot_water(self, p):      return f"Setting the heat pump's hot water to {p}"
