from datetime import datetime, timezone
from typing import List, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.tabs.AuthTab import AuthTab, BACKSPACE_KEYS, ENTER_KEYS, PENDING
from interfaces.console.widgets.Board import Fact, SummaryTile
from common.logging import info, trace
from integrations.volvo.VolvoIntegration import VolvoIntegration

ENTRY_TAIL = 60             # how much of the pasted address the entry row shows


class VolvoTab(AuthTab):
    """The Volvo sign-in: whether the Volvo ID is signed in and how many cars are configured,
    and the browser sign-in — the URL to open, then the address the browser lands on pasted
    back — or its sign-out."""

    integration = VolvoIntegration

    HELP = "Ctrl-R refresh  Ctrl-L sign in  Ctrl-E paste the address  Ctrl-O sign out"
    SUBMITTING_MESSAGE = "Signing in with the pasted address..."
    STATUS_LABELS = {
        **AuthTab.STATUS_LABELS,
        "needs_auth": ("Sign-in needed", Theme.COLOR_YELLOW),
    }

    def __init__(self):
        super().__init__()
        self._cars: list = []
        self._sign_in = None            # the SignIn whose URL the user is working through

    def _on_status(self, api):
        try:
            self._cars = api.vehicles()
        except Exception as e:
            trace(f"Volvo tab: the cars could not be listed: {e}")
            self._cars = []

    def _expiry(self, api) -> str:
        try:
            expires = api.auth.expires_at()
        except Exception as e:
            trace(f"Volvo tab: no expiry known: {e}")
            return PENDING
        if expires is None:
            return PENDING
        left = (expires - datetime.now(timezone.utc)).total_seconds()
        return "refresh due" if left <= 0 else f"token {int(left // 60)} min"

    def _tiles(self) -> List[SummaryTile]:
        api = self._api
        tiles: List[SummaryTile] = []
        if api is not None:
            if not api.signed_in():
                tiles.append(("Volvo ID", "not signed in", Theme.COLOR_YELLOW, [("Ctrl-L to start", Theme.COLOR_LABEL)]))
            elif api.auth.needs_sign_in_again():
                tiles.append(("Volvo ID", "sign in again", Theme.COLOR_RED, [("the sign-in lapsed", Theme.COLOR_LABEL)]))
            else:
                tiles.append(("Volvo ID", "signed in", Theme.COLOR_GREEN, [(self._expiry(api), Theme.COLOR_DIM)]))
        names = ", ".join(spec.label for spec in self._cars) or "none under volvo.vehicles"
        tiles.append(("Cars", str(len(self._cars)), Theme.COLOR_GREEN if self._cars else Theme.COLOR_DIM, [(names, Theme.COLOR_LABEL)]))
        return tiles

    def _fields(self) -> List[Fact]:
        if self._input_mode:
            shown = self._input_text if len(self._input_text) <= ENTRY_TAIL else "…" + self._input_text[-ENTRY_TAIL:]
            return [Fact("Address", shown or "paste the address the browser landed on", Theme.COLOR_BRIGHT, waiting=True, bold=True)]
        if self._sign_in is not None:
            rows = self._link_rows(self._sign_in.url)
            return ([Fact("Open" if i == 0 else "", row, Theme.COLOR_BRIGHT, bold=True) for i, row in enumerate(rows)]
                    + [Fact("Then", "Ctrl-E, paste the address the browser lands on, Enter", Theme.COLOR_YELLOW, waiting=True)])
        hint = self._entry_hint()
        return [Fact("Sign-in", hint[0] if hint else PENDING, hint[1] if hint else Theme.COLOR_FAINT)]

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        api = self._api
        if api is None:
            return None
        if api.signed_in() and not api.auth.needs_sign_in_again():
            return "signed in — Ctrl-L signs in again, Ctrl-O signs out", Theme.COLOR_GREEN
        return "Ctrl-L shows the sign-in link", Theme.COLOR_YELLOW

    # --- keys --------------------------------------------------------------------------------

    def _handle_key(self, key: str) -> bool:
        if key == '\x0c':   # Ctrl+L
            self._begin_sign_in()
            return True
        if key == '\x0f':   # Ctrl+O
            self._sign_out()
            return True
        return super()._handle_key(key)        # Ctrl+E opens the entry for the pasted address

    def _handle_entry_key(self, key: str) -> bool:
        """The pasted address, however long: any printable key appends (a paste arrives as
        many of them), Enter submits, Backspace edits, Esc cancels."""
        if key == 'escape':
            self._cancel_entry()
        elif key in ENTER_KEYS:
            if self._input_text.strip():
                self._submit_code()
        elif key in BACKSPACE_KEYS:
            self._input_text = self._input_text[:-1]
        elif len(key) == 1 and key.isprintable():
            self._input_text += key
        return True

    def _begin_sign_in(self):
        def sign_in(api):
            self._sign_in = api.login_begin()
            self._status_message = "Waiting — open the link, sign in with the Volvo ID, then Ctrl-E and paste the address"
            info(f"Volvo tab: sign-in link: {self._sign_in.url}")

        self._act("Signing in...", sign_in)

    def _verify_code(self, api, code: str) -> None:
        failure = api.login_finish(code)
        if failure:
            self._status_message = f"Sign-in failed: {failure}"
            info(f"Volvo tab: sign-in failed: {failure}")
        else:
            self._sign_in = None
            self._status_message = "Signed in successfully"
            self._check_health_now()
        self._check_auth_status()

    def _sign_out(self):
        def sign_out(api):
            api.sign_out()
            self._sign_in = None
            self._status_message = "Signed out"
            self._check_auth_status()

        self._act("Signing out...", sign_out)

    def _refresh(self):
        self._status_message = "Refreshing..."
        self._check_auth_status_async()
