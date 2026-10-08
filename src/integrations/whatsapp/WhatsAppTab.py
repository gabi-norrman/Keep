from typing import List, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.tabs.AuthTab import AuthTab, BACKSPACE_KEYS, ENTER_KEYS, PENDING, STANDARD_ENTRY_HINT
from interfaces.console.widgets import Board
from interfaces.console.widgets.Board import Fact, SummaryTile
from common.logging import trace
from integrations.whatsapp.WhatsAppIntegration import WhatsAppIntegration

INPUT_PHONE = "phone"
PHONE_MAX = 16
PHONE_MIN = 8
CURSOR = "▏"
LINK_HINT = "WhatsApp on the phone: Linked devices > Link a device > Link with phone number"

FEED_COLORS = {"live": Theme.COLOR_GREEN, "catching_up": Theme.COLOR_YELLOW, "starting": Theme.COLOR_YELLOW,
               "failed": Theme.COLOR_RED}


class WhatsAppTab(AuthTab):
    """The WhatsApp link: the phone number, then the pairing code to type into the phone."""

    integration = WhatsAppIntegration

    HELP = "Ctrl-R reconnect  Ctrl-P link the account"
    STATUS_LABELS = {
        **AuthTab.STATUS_LABELS,
        "needs_auth": ("Not linked", Theme.COLOR_YELLOW),
        "connecting": ("Connecting", Theme.COLOR_YELLOW),
    }

    def __init__(self):
        super().__init__()
        self._phone_number = ""
        self._code: Optional[str] = None       # the pairing code shown until the phone accepts it
        self._feed: Optional[dict] = None

    def _on_status(self, api):
        from assistant.integration.FeedRunner import Feeds
        try:
            self._feed = Feeds.get_instance().status(api.feed_name)
        except Exception as e:
            trace(f"WhatsAppTab: no feed status: {e}")
            self._feed = None

    def _tiles(self) -> List[SummaryTile]:
        feed = self._feed
        if not feed:
            return [("Feed", PENDING, Theme.COLOR_DIM, [("not running", Theme.COLOR_DIM)])]
        state = feed["state"]
        segments = [(f"{feed['stored']:,} {feed['items']} stored", Theme.COLOR_LABEL)]
        if feed.get("last_error"):
            segments = [(str(feed["last_error"]), Theme.COLOR_RED)]
        return [("Feed", "paused" if feed.get("paused") else state.replace("_", " "),
                 FEED_COLORS.get(state, Theme.COLOR_DIM), segments)]

    def _fields(self) -> List[Fact]:
        """The two steps: the phone while it is typed, then the code the phone is waiting for."""
        if self._input_mode == INPUT_PHONE:
            return Board.steps(0, [("Phone", self._input_text + CURSOR), ("Code", PENDING)])
        if self._code:
            return Board.steps(1, [("Phone", self._phone_number), ("Code", f"{self._code}  —  {LINK_HINT}")])
        hint = self._entry_hint()
        return [Fact("Phone", hint[0], hint[1]) if hint else Fact("Phone", PENDING, Theme.COLOR_FAINT),
                Fact("Code", PENDING, Theme.COLOR_FAINT)]

    def _hints(self) -> str:
        if self._input_mode == INPUT_PHONE:
            return f"With the country code, e.g. +46701234567  {STANDARD_ENTRY_HINT}"
        return super()._hints()

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        if self.status == "needs_auth":
            return "Ctrl-P starts the link", Theme.COLOR_YELLOW
        return None

    def _handle_key(self, key: str) -> bool:
        if key == '\x10':  # Ctrl+P
            self._input_mode = INPUT_PHONE
            self._input_text = "+"
            self._status_message = ""
            self._code = None
            return True
        return False

    def _handle_entry_key(self, key: str) -> bool:
        text = self._input_text
        if key == 'escape':
            self._cancel_entry()
        elif key in ENTER_KEYS:
            if len(text) >= PHONE_MIN:
                self._submit_phone()
            else:
                trace("WhatsAppTab: Enter ignored — phone too short")
        elif key in BACKSPACE_KEYS:
            if len(text) > 1:       # the phone keeps its '+'
                self._input_text = text[:-1]
        elif key.isdigit() and len(text) < PHONE_MAX:
            self._input_text += key
        return True

    def _submit_phone(self):
        phone = self._input_text
        self._phone_number = phone

        def link(api):
            code, failure = api.login(phone)
            if failure:
                self._status_message = failure
                return
            self._code = code
            self._status_message = "Waiting for the phone to accept the code..."
            failure = api.wait_paired()
            self._code = None
            if failure:
                self._status_message = f"Error: {failure}"
                return
            api.finish_login()
            self._status_message = "Linked — the feed is starting"
            self._check_auth_status()

        self._act("Asking WhatsApp for a pairing code...", link)

    def _refresh(self):
        self._status_message = "Reconnecting..."

        def reconnect():
            from assistant.integration.IntegrationMonitor import IntegrationMonitor
            try:
                IntegrationMonitor.get_instance().restart(self.integration.config_name())
                self._status_message = "Reconnect initiated"
            except Exception as e:
                self._status_message = f"Error: {e}"
            self._check_auth_status()

        self._run(reconnect)
