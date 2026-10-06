from assistant.commands.CommandSet import CommandSet
from integrations.x.XIntegration import XIntegration


class XCommands(CommandSet):
    """Searching X (Twitter) posts."""

    @staticmethod
    def _get_x_integration() -> XIntegration:
        return XIntegration.get_instance()

    def cmd_search_x(self, parameter_string: str):
        self.background_command(self._get_x_integration().search, parameter_string)

    def describe_search_x(self, p):               return f"Searching X for {p.strip()}" if p.strip() else "Searching X"
