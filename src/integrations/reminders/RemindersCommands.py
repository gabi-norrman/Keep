from assistant.commands.CommandSet import CommandSet
from integrations.reminders.RemindersIntegration import add_reminder_item


class RemindersCommands(CommandSet):
    """Adding to the shared shopping list in Reminders."""

    def cmd_add_shopping_list_item(self, parameter_string: str) -> str:
        return add_reminder_item(parameter_string)

    def describe_add_shopping_list_item(self, p): return f"Adding {p.strip()} to the shopping list"
