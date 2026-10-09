from assistant.ChannelProfile import SOURCE_CONSOLE
from assistant.commands.CommandSet import CommandSet
from assistant.commands.Commands import normalize_llm_escapes
from assistant.Notifier import notifier
from common.logging import info, trace


class TelegramFeedCommands(CommandSet):
    """Signs the Telegram Feed's account in from the console chat and sends a Telegram message
    from it (sensitive, so it passes the approval gate first)."""
    MULTI_PARAM_COMMANDS = frozenset({"send_telegram"})
    SENSITIVE_COMMANDS = frozenset({"send_telegram"})

    def _feed(self):
        """The running Telegram Feed, or None with why not (its state, for the reply)."""
        integration = self.commands.integration_monitor().find(self.commands.TELEGRAM_FEED)
        if integration is None or not hasattr(integration, "send"):
            state = getattr(integration, "state", None)
            return None, self.commands.state_label(getattr(state, "value", state), "not available")
        return integration, None

    # --- send_telegram ----------------------------------------------------------------------------

    def cmd_send_telegram(self, *args: str) -> str:
        if len(args) < 2:
            return "send_telegram failed: expected 'recipient, message' parameters"
        recipient = args[0].strip()
        body = ", ".join(a for a in args[1:]).strip()    # an unquoted body arrives split on its commas
        if not recipient or not body:
            return "send_telegram failed: expected 'recipient, message' parameters"
        feed, state = self._feed()
        if feed is None:
            info(f"send_telegram: Telegram Feed is {state}, nothing sent")
            return f"send_telegram failed: Telegram Feed is {state} — it sends from the user's own account, see the setup toolkit"
        body = self.commands.with_ai_footer(normalize_llm_escapes(body))
        return feed.send(recipient, body)

    # The approval card names the chat the message goes to, or why there is none.
    def describe_send_telegram(self, p):
        recipient = p.split(",", 1)[0].strip().strip('"\'') if p else ""
        if not recipient:
            return "Sending Telegram message"
        try:
            feed, _ = self._feed()
            if feed is not None:
                entity, display, err = feed.resolve_recipient(recipient)
                if err:
                    return f"Sending Telegram message to {recipient} — {err}"
                username = getattr(entity, "username", None)
                return f"Sending Telegram message to {display}" + (f" (@{username})" if username else "")
        except Exception as e:
            trace(f"describe_send_telegram: lookup failed for '{recipient}': {e}")
        return f"Sending Telegram message to {recipient}"

    # --- telegram_login ---------------------------------------------------------------------------

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
