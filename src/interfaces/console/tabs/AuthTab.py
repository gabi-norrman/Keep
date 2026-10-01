"""The Integrations tabs that sign a cloud account in: iCloud, Arlo, Ring and Telegram.

They share one screen: the title, the connection status with its message, a section of
the tab's own (its device list), then either the code-entry prompt or a hint on how to
start it, the outcome of the last action, and a help line. The status check runs on a
background thread every CHECK_INTERVAL seconds and when the tab is opened. A subclass
says how to reach its integration, what to draw in its section and how to verify a code;
the key handling for a six-digit code is here too.
"""
import threading
import time
from typing import Callable, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.tabs.TabView import TabView, SUB_CONTENT_TOP
from common.logging import info, trace

COLOR_GREEN = Theme.COLOR_GREEN
COLOR_YELLOW = Theme.COLOR_YELLOW
COLOR_RED = Theme.COLOR_RED
COLOR_GRAY = Theme.COLOR_LABEL
COLOR_WHITE = Theme.COLOR_BRIGHT
COLOR_HELP = Theme.COLOR_DIM

# The names TUIFloxyConsole._parse_key gives these keys, plus the raw bytes some paths pass
ENTER_KEYS = ('\r', '\n', 'enter')
BACKSPACE_KEYS = ('\x7f', '\x08', 'backspace')

# Words in an action's outcome that colour it green (done) or yellow (still going); any
# other outcome reads as a failure, in red.
DONE_WORDS = ("success", "refreshed", "verified", "auto-detected", "connected")
BUSY_WORDS = ("verifying", "connecting", "waiting", "sending", "signing")

CODE_LENGTH = 6
STANDARD_ENTRY_HINT = "Press Enter to submit, Esc to cancel"


def code_display(code: str, length: int) -> str:
    """The digits typed so far, spaced out, with a slot for each one still to come."""
    shown = " ".join(code) if code else "_"
    return shown + " _" * (length - len(code))


class AuthTab(TabView):
    TITLE = ""
    HELP = " Ctrl-R refresh  Ctrl-E enter 2FA code "
    CHECK_INTERVAL = 30.0
    SUBMITTING_MESSAGE = "Submitting 2FA code..."
    # The "Status:" line per auth status; anything else reads as not initialized
    STATUS_LABELS = {
        "authenticated": ("Connected", COLOR_GREEN),
        "error": ("Error", COLOR_RED),
    }

    def __init__(self):
        self._api = None
        self._auth_status: dict = {"status": "not_initialized", "message": "Not checked yet"}
        self._status_message = ""      # the outcome of the last action
        self._input_mode = False       # truthy while the tab is taking typed input
        self._input_text = ""
        self._last_check_time = 0.0
        self._checking = False

    # --- what a subclass provides ------------------------------------------------------------

    def _connect(self):
        """The integration singleton; raises when it cannot be built (not configured)."""
        raise NotImplementedError

    def _on_status(self, api) -> None:
        """After a status check: fetch whatever the tab's section shows (device names)."""

    def _draw_section(self, screen: TUIScreen, y: int) -> int:
        """Draw the tab's own section below the status message at row `y`; return the last
        row used (`y` when there is nothing to draw)."""
        return y

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        """What to say in place of the entry prompt while no input is being taken."""
        return None

    def _verify_code(self, api, code: str) -> None:
        """Submit `code` to the integration on the worker thread and set the outcome."""
        raise NotImplementedError

    def _refresh(self) -> None:
        """Ctrl+R."""
        raise NotImplementedError

    # --- status ------------------------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._auth_status.get("status", "unknown")

    def _get_api(self):
        if self._api is not None and self._rebuilt(self._api):
            trace(f"{self.name} tab: the integration was rebuilt — dropping the cached instance")
            self._api = None
        if self._api is None:
            try:
                self._api = self._connect()
            except Exception as e:
                # Asked every frame by some tabs: log a failure once, not thirty times a second
                if self._auth_status.get("message") != str(e):
                    trace(f"{self.name} tab: integration unavailable: {e}")
                self._auth_status = {"status": "error", "message": str(e)}
        return self._api

    @staticmethod
    def _rebuilt(api) -> bool:
        """True when *api* is a singleton the monitor has since replaced (a restart after
        config_request): a tab built before the section existed must not keep refreshing,
        or send a 2FA code to, the unconfigured instance."""
        peek = getattr(type(api), "peek_instance", None)
        return peek is not None and peek() is not api

    def _check_auth_status(self):
        api = self._get_api()
        if api:
            self._auth_status = api.get_auth_status()
            self._on_status(api)
        self._last_check_time = time.time()

    def _check_auth_status_async(self):
        """Run _check_auth_status on a background thread, one at a time."""
        if self._checking:
            return
        self._checking = True

        def check():
            try:
                self._check_auth_status()
            finally:
                self._checking = False

        self._run(check)

    @staticmethod
    def _run(fn: Callable[[], None]):
        threading.Thread(target=fn, daemon=True).start()

    # --- drawing -----------------------------------------------------------------------------

    def _before_draw(self):
        """Per-frame hook ahead of drawing (the iCloud and Ring tabs pick up the auto-2FA outcome)."""

    def draw(self, screen: TUIScreen, frame: int):
        if time.time() - self._last_check_time > self.CHECK_INTERVAL:
            self._check_auth_status_async()
        self._before_draw()

        screen.reset_color()
        y = SUB_CONTENT_TOP
        screen.set_color(COLOR_WHITE)
        screen.bold()
        screen.write_at(2, y, self.TITLE)
        screen.reset_color()

        y += 2
        screen.write_at(2, y, "Status: ")
        if self._checking:
            label, color = "Checking...", COLOR_YELLOW
        else:
            label, color = self.STATUS_LABELS.get(self.status, ("Not initialized", COLOR_GRAY))
        screen.set_color(color)
        screen.write_at(10, y, f"● {label}")
        screen.reset_color()

        y += 1
        message = self._auth_status.get("message", "")
        if message:
            screen.set_color(COLOR_GRAY)
            screen.write_at(2, y, message[:screen.width - 4])

        y = self._draw_section(screen, y) + 2
        y = self._draw_entry(screen, y) + 2

        outcome = self._outcome()
        if outcome:
            screen.set_color(self._outcome_color(outcome))
            screen.write_at(2, y, outcome[:screen.width - 4])

        screen.reset_color()
        screen.set_color(COLOR_HELP)
        screen.write_at(2, screen.height - 1, self.HELP)

    def _draw_entry(self, screen: TUIScreen, y: int) -> int:
        """The entry prompt while input is being taken, else the hint; returns the last row used."""
        if self._input_mode:
            return self._draw_prompt(screen, y, "Enter 2FA code: ", code_display(self._input_text, CODE_LENGTH),
                                     STANDARD_ENTRY_HINT)
        hint = self._entry_hint()
        if hint:
            text, color = hint
            screen.set_color(color)
            screen.write_at(2, y, text)
        return y

    @staticmethod
    def _draw_prompt(screen: TUIScreen, y: int, label: str, value: str, hint: str) -> int:
        """A yellow label, the value typed so far after it, and a dim hint on the next row."""
        screen.set_color(COLOR_YELLOW)
        screen.write_at(2, y, label)
        screen.set_color(COLOR_WHITE)
        screen.write_at(2 + len(label), y, value)
        screen.set_color(COLOR_GRAY)
        screen.write_at(2, y + 1, hint)
        return y + 1

    def _outcome(self) -> str:
        """The outcome line to show (the Arlo tab swaps in its rate-limit countdown)."""
        return self._status_message

    @staticmethod
    def _outcome_color(outcome: str) -> Color:
        lowered = outcome.lower()
        if any(w in lowered for w in DONE_WORDS):
            return COLOR_GREEN
        if any(w in lowered for w in BUSY_WORDS):
            return COLOR_YELLOW
        return COLOR_RED

    # --- keys --------------------------------------------------------------------------------

    def handle_input(self, key: str) -> bool:
        if self._input_mode:
            return self._handle_entry_key(key)
        if key == '\x12':  # Ctrl+R
            self._refresh()
            return True
        return self._handle_key(key)

    def _handle_key(self, key: str) -> bool:
        """The tab's own shortcuts outside entry mode; Ctrl+E starts the code entry."""
        if key == '\x05':
            self._begin_entry()
            return True
        return False

    def _begin_entry(self):
        self._input_mode = True
        self._input_text = ""

    def _cancel_entry(self):
        self._input_mode = False
        self._input_text = ""
        self._status_message = ""

    def _handle_entry_key(self, key: str) -> bool:
        """A six-digit code: digits fill it (submitted when complete), Enter submits a complete
        one, Backspace edits, Esc cancels. Every key is consumed while entering."""
        if key == 'escape':
            self._cancel_entry()
        elif key in ENTER_KEYS:
            if len(self._input_text) == CODE_LENGTH:
                self._submit_code()
        elif key in BACKSPACE_KEYS:
            self._input_text = self._input_text[:-1]
        elif key.isdigit() and len(self._input_text) < CODE_LENGTH:
            self._input_text += key
            if len(self._input_text) == CODE_LENGTH:
                self._submit_code()
        return True

    def _submit_code(self):
        code = self._input_text
        self._act(self.SUBMITTING_MESSAGE, lambda api: self._verify_code(api, code))

    def _act(self, message: str, body: Callable[[object], None]):
        """Leave entry mode and run `body(api)` on a worker thread with `message` as the
        outcome meanwhile; an exception becomes the outcome."""
        api = self._get_api()
        if not api:
            self._status_message = "Error: API not initialized"
            self._input_mode = False
            return
        self._input_mode = False
        self._input_text = ""
        self._status_message = message

        def run():
            try:
                body(api)
            except Exception as e:
                self._status_message = f"Error: {e}"
                info(f"{self.name} tab: {message.rstrip('.')} failed: {e}")

        self._run(run)

    # --- tab lifecycle -----------------------------------------------------------------------

    def on_activate(self):
        self._check_auth_status_async()

    def on_deactivate(self):
        self._input_mode = False
        self._input_text = ""
