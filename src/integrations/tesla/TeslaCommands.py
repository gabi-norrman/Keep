from assistant.commands.CommandSet import CommandSet
from integrations.tesla.TeslaIntegration import TeslaIntegration, TeslaAPIError


class TeslaCommands(CommandSet):
    """The cars' commands: max defrost, climate on and off, the trunk."""

    # Looked up per call: get_instance() is the cache, so a restarted integration
    # (restart_integration) is what the next command uses.
    @staticmethod
    def _get_tesla_api() -> TeslaIntegration:
        return TeslaIntegration.get_instance()

    def _run_tesla_command(self, tesla_method, parameter_string: str) -> str:
        try:
            tesla_method(parameter_string if parameter_string else None)
            return ""
        except TeslaAPIError as e:
            return f"Tesla command failed: {e}"

    def cmd_tesla_max_defrost(self, parameter_string: str):
        self.background_command(
            lambda p: self._run_tesla_command(self._get_tesla_api().set_max_defrost, p),
            parameter_string
        )

    def cmd_tesla_air_con_on(self, parameter_string: str):
        self.background_command(
            lambda p: self._run_tesla_command(self._get_tesla_api().start_air_conditioning, p),
            parameter_string
        )

    def cmd_tesla_air_con_off(self, parameter_string: str):
        self.background_command(
            lambda p: self._run_tesla_command(self._get_tesla_api().stop_air_conditioning, p),
            parameter_string
        )

    def cmd_tesla_open_trunk(self, parameter_string: str):
        self.background_command(
            lambda p: self._run_tesla_command(self._get_tesla_api().open_trunk, p),
            parameter_string
        )

    def describe_tesla_max_defrost(self, p):      return "Turning on Tesla max defrost"
    def describe_tesla_air_con_on(self, p):       return "Turning on Tesla A/C"
    def describe_tesla_air_con_off(self, p):      return "Turning off Tesla A/C"
    def describe_tesla_open_trunk(self, p):       return "Opening Tesla trunk"
