from assistant.commands.CommandSet import CommandSet
from integrations.caldav.CalDAVIntegration import CalDAVIntegration


class CalDAVCommands(CommandSet):
    """The calendar commands: create an event, load named calendars."""
    MULTI_PARAM_COMMANDS = frozenset({"create_event", "load_calendar"})
    # Side-effect free, so the prompt test suite still runs it (see Commands.PURE_COMMANDS).
    PURE_COMMANDS = frozenset({"load_calendar"})

    def _run_create_event(self, *args: str) -> str:
        try:
            if len(args) < 4:
                return "create_event failed: expected 4 parameters (title, date, start_time, end_time)"
            # If the LLM under-quoted and produced extra tokens, the title may have
            # been split at internal commas — re-join leading tokens into the title
            # and pull the last 3 tokens as date/start/end.
            title = ", ".join(a.strip() for a in args[:-3])
            date_str = args[-3].strip()
            start_time = args[-2].strip()
            end_time = args[-1].strip()
            CalDAVIntegration.create_calendar_event(title, date_str, start_time, end_time)
            return ""
        except Exception as e:
            return f"create_event failed: {e}"

    def cmd_create_event(self, *args: str):
        self.background_command(self._run_create_event, list(args))

    def cmd_load_calendar(self, *names: str):
        self.background_command(self._run_load_calendar, list(names))

    @staticmethod
    def _run_load_calendar(*names: str) -> str:
        """The named calendars over the window the prompt's calendar section covers."""
        names = [n for n in names if n and n.strip()]
        if not names:
            return "load_calendar failed: expected a calendar's name"
        try:
            return CalDAVIntegration.load_calendars(names)
        except Exception as e:
            return f"load_calendar failed: {e}"

    def describe_load_calendar(self, p):          return f"Looking at the calendar: {self.commands.first_argument(p)}" if p.strip() else "Looking at a calendar"

    def describe_create_event(self, p):
        title = p.split(",", 1)[0].strip()
        return f"Creating calendar event '{title}'" if title else "Creating calendar event"
