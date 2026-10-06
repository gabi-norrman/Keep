from assistant.ChannelProfile import SOURCE_CONSOLE
from assistant.commands.CommandSet import CommandSet
from assistant.Notifier import notifier
from common.logging import info


class TelegramFeedCommands(CommandSet):
    """Signs the Telegram Feed's account in from the console chat."""

    def cmd_telegram_login(self, parameter_string: str):
        """Signs the Telegram Feed's account in from the chat: the code Telegram sends and
        the account's 2FA password come in through the masked prompt, so the LLM sees neither.
        Console only — Telegram invalidates a login code that is sent as a Telegram message,
        so the bot chat cannot carry it."""
        phone = parameter_string.strip().strip('"\'').replace(" ", "")
        if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 8:
            return ('telegram_login failed: expected the phone number with its country code, '
                    'e.g. telegram_login("+46701234567")')
        ctx = self.context
        source = getattr(ctx, "source", None)
        if source != SOURCE_CONSOLE:
            info(f"telegram_login: refused from {source or 'no chat'} — console only")
            return ("telegram_login failed: it only works from the console chat — Telegram invalidates a login "
                    "code that is sent as a Telegram message, so the code cannot be typed into this chat. "
                    "Ask the user to open the console and say it there.")
        conversation = getattr(ctx, "conversation", None)
        self.background_command(self._run_telegram_login, (phone, source, conversation), lane=self.commands.CONFIG_LANE)

    def _run_telegram_login(self, phone: str, source=None, conversation=None) -> str:
        monitor = self.commands.integration_monitor()
        integration = monitor.find(self.commands.TELEGRAM_FEED)
        if integration is None or not hasattr(integration, "login"):
            state = getattr(integration, "state", None)
            label = self.commands.state_label(getattr(state, "value", state), "not available")
            info(f"telegram_login: Telegram Feed is {label}, no login possible")
            if label == "disabled":
                return f'Telegram Feed is disabled — enable_integration("{self.commands.TELEGRAM_FEED}") first.'
            return f"Telegram Feed is {label} — set telegram_feed.api_id and api_hash and enable it first."

        def ask(title: str, details: str, accept) -> str:
            return notifier().request_secret(title=title, details=details, accept=accept,
                                             source=source, conversation=conversation)

        failure = integration.login(phone, ask)
        if failure:
            info(f"telegram_login: {failure}")
            return failure
        result = monitor.restart(integration.name)
        return f"Telegram Feed signed in. {self.commands.describe_restart(result['status'], result['restartable'])}"

    def describe_telegram_login(self, p):     return "Signing the Telegram account in"
