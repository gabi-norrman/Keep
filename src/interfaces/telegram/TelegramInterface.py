"""The Telegram interface: the bot people chat with, registered as a transport on the
process-wide ChatHub (which is the Notifier: 2FA prompts, autonomous findings, photos,
approval gate). Enabled when `interfaces.telegram.enabled` is true; without a
`telegram_bot.bot_token` it still starts, as a "Telegram Chat" integration the monitor lists as not
configured, so the assistant can store the token (config_request) and bring the bot up with
`restart_integration("Telegram Chat")` — no server restart."""
from common.logging import info
from interfaces.Interface import Interface


class TelegramInterface(Interface):
    """Starts the Telegram transport and installs the ChatHub as the process-wide Notifier."""

    name = "telegram"

    def __init__(self):
        self.transport = None
        self.hub = None

    def start(self, assistant, app) -> None:
        from assistant.integration.IntegrationMonitor import IntegrationMonitor
        from assistant.Notifier import set_notifier
        from interfaces.chat.ChatHub import ChatHub
        from interfaces.telegram.TelegramTransport import TelegramTransport

        self.hub = ChatHub.get_instance()
        self.hub.attach(assistant)
        set_notifier(self.hub)
        # By class, so the monitor can rebuild it: `_start_transport` runs now and on every
        # restart_integration("Telegram Chat"). A chat interface, not a context feed — it lives
        # outside the integrations/ walk.
        IntegrationMonitor.get_instance().register_class(TelegramTransport, start=self._start_transport)
        info("Telegram interface started.")

    def _start_transport(self, transport) -> None:
        if self.transport is not None and self.transport is not transport:
            self.hub.unregister(self.transport)   # the rebuilt bot replaces the old one on the hub
        self.transport = transport
        if not transport.is_configured:
            info("Telegram bot has no telegram_bot.bot_token yet — listed as not configured, "
                 "restart_integration(\"Telegram Chat\") starts it once one is stored.")
            return
        self.hub.register(transport)
        transport.start(self.hub)

    def stop(self) -> None:
        if self.transport is not None:
            try:
                self.hub.unregister(self.transport)
                self.transport.stop()
            except Exception:
                pass
