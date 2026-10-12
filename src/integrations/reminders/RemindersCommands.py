from assistant.commands.CommandSet import CommandSet
from integrations.reminders.RemindersIntegration import add_reminder_item, complete_reminder, list_open_reminders


class RemindersCommands(CommandSet):
    """The Reminders commands: the open to-dos of every list, adding one to a list or to the
    shared shopping list, and ticking one off."""
    MULTI_PARAM_COMMANDS = frozenset({"add_reminder", "complete_reminder"})
    PURE_COMMANDS = frozenset({"list_reminders"})

    def cmd_list_reminders(self, parameter_string: str = ""):
        self.background_command(list_open_reminders, parameter_string.strip() or None)

    def cmd_add_shopping_list_item(self, parameter_string: str) -> str:
        return add_reminder_item(parameter_string)

    def cmd_add_reminder(self, *args: str) -> str:
        list_name, item = self._list_and_item(args)
        if item is None:
            return "add_reminder failed: expected 'list, item'"
        return add_reminder_item(item, list_name)

    def cmd_complete_reminder(self, *args: str) -> str:
        list_name, item = self._list_and_item(args)
        if item is None:
            return "complete_reminder failed: expected 'list, item'"
        return complete_reminder(item, list_name)

    @staticmethod
    def _list_and_item(args) -> tuple[str, str | None]:
        """The list, then the item — everything after the list rejoined, so an item the LLM
        under-quoted with a comma in it survives the split."""
        args = [a.strip() for a in args]
        if len(args) < 2 or not args[0] or not any(args[1:]):
            return "", None
        return args[0], ", ".join(a for a in args[1:] if a)

    def describe_list_reminders(self, p): return f"Reading the {p.strip()} list" if p.strip() else "Reading the reminders lists"
    def describe_add_shopping_list_item(self, p): return f"Adding {p.strip()} to the shopping list"
    def describe_add_reminder(self, p): return f"Adding a reminder: {p.strip()}"
    def describe_complete_reminder(self, p): return f"Ticking off a reminder: {p.strip()}"
