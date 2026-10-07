from datetime import datetime, timezone
from typing import List, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.tabs.AuthTab import AuthTab, PENDING
from interfaces.console.widgets.Board import Fact, SummaryTile, Table
from common.logging import info, trace
from integrations.microsoft.MicrosoftIntegration import MicrosoftIntegration

NAME_WIDTH, ADDRESS_WIDTH, GIVES_WIDTH, STATE_WIDTH = 16, 30, 16, 14


class MicrosoftTab(AuthTab):
    """The Microsoft 365 accounts: which are signed in, and the device-code sign-in of the
    selected one — the code and URL to type it at — or its sign-out."""

    integration = MicrosoftIntegration

    HELP = "Ctrl-R refresh  Ctrl-L sign in  Ctrl-O sign out  ↑↓ select account"
    STATUS_LABELS = {
        **AuthTab.STATUS_LABELS,
        "needs_auth": ("Sign-in needed", Theme.COLOR_YELLOW),
    }

    def __init__(self):
        super().__init__()
        self._accounts: list = []
        self._selected = 0
        self._flow = None            # the DeviceFlow the user is typing the code of
        self._flow_account = ""

    def _on_status(self, api):
        self._accounts = api.accounts()
        self._selected = min(self._selected, max(0, len(self._accounts) - 1))

    def _selected_account(self):
        return self._accounts[self._selected] if self._accounts else None

    @staticmethod
    def _expiry(account) -> str:
        try:
            expires = account.auth.expires_at()
        except Exception as e:
            trace(f"Microsoft tab: no expiry for {account.name}: {e}")
            return PENDING
        if expires is None:
            return PENDING
        left = (expires - datetime.now(timezone.utc)).total_seconds()
        return "refresh due" if left <= 0 else f"token {int(left // 60)} min"

    def _table(self) -> Table:
        rows = []
        for i, account in enumerate(self._accounts):
            signed_in = account.signed_in()
            gives = " + ".join(part for part, on in (("mail", account.mail), ("calendar", account.calendar)) if on)
            marker = "▸ " if i == self._selected else "  "
            rows.append((Theme.COLOR_GREEN if signed_in else Theme.COLOR_YELLOW, [
                (marker + account.name, Theme.COLOR_BRIGHT if i == self._selected else Theme.COLOR_TEXT),
                (account.address or PENDING, Theme.COLOR_LABEL),
                (gives, Theme.COLOR_TEAL),
                ("signed in", Theme.COLOR_GREEN) if signed_in else ("not signed in", Theme.COLOR_YELLOW),
                (self._expiry(account) if signed_in else "", Theme.COLOR_DIM)]))
        return Table("Accounts",
                     [("Account", NAME_WIDTH), ("Address", ADDRESS_WIDTH), ("Gives", GIVES_WIDTH), ("State", STATE_WIDTH), ("Token", None)],
                     rows, "no account under microsoft.accounts")

    def _tiles(self) -> List[SummaryTile]:
        signed_in = sum(1 for a in self._accounts if a.signed_in())
        if not self._accounts:
            return [("Accounts", PENDING, Theme.COLOR_DIM, [("none configured", Theme.COLOR_DIM)])]
        color = Theme.COLOR_GREEN if signed_in == len(self._accounts) else Theme.COLOR_YELLOW
        return [("Accounts", f"{signed_in}/{len(self._accounts)}", color, [("signed in", Theme.COLOR_LABEL)])]

    def _fields(self) -> List[Fact]:
        account = self._selected_account()
        name = account.name if account else PENDING
        if self._flow is not None:
            return [Fact("Account", self._flow_account, Theme.COLOR_TEXT),
                    Fact("Code", self._flow.user_code, Theme.COLOR_BRIGHT, waiting=True, bold=True),
                    Fact("Type it at", self._flow.verification_uri, Theme.COLOR_TEXT)]
        hint = self._entry_hint()
        return [Fact("Account", name, Theme.COLOR_TEXT),
                Fact("Code", hint[0] if hint else "not asked for", hint[1] if hint else Theme.COLOR_FAINT),
                Fact("Type it at", PENDING, Theme.COLOR_FAINT)]

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        account = self._selected_account()
        if account is None:
            return None
        if account.signed_in():
            return "signed in — Ctrl-L signs in again, Ctrl-O signs out", Theme.COLOR_GREEN
        return "Ctrl-L starts the sign-in", Theme.COLOR_YELLOW

    def handle_input(self, key: str) -> bool:
        if key in ('up', 'down') and self._accounts:
            self._selected = (self._selected + (1 if key == 'down' else -1)) % len(self._accounts)
            return True
        return super().handle_input(key)

    def _handle_key(self, key: str) -> bool:
        if key == '\x0c':   # Ctrl+L
            self._begin_sign_in()
            return True
        if key == '\x0f':   # Ctrl+O
            self._sign_out()
            return True
        return False

    def _begin_sign_in(self):
        account = self._selected_account()
        if account is None:
            self._status_message = "No account to sign in"
            return
        if self._flow is not None:
            self._status_message = f"Waiting for the code of {self._flow_account} to be typed"
            return
        name = account.name

        def sign_in(api):
            flow = api.login_begin(name)
            self._flow, self._flow_account = flow, name
            self._status_message = f"Waiting — {flow.instructions}"
            try:
                failure = api.login_finish(name, flow)
            finally:
                self._flow = None
            if failure:
                self._status_message = f"Sign-in of {name} failed: {failure}"
                info(f"Microsoft tab: sign-in of {name} failed: {failure}")
            else:
                self._status_message = f"{name} signed in successfully"
                self._check_health_now()
            self._check_auth_status()

        self._act(f"Signing {name} in...", sign_in)

    def _sign_out(self):
        account = self._selected_account()
        if account is None:
            return
        name = account.name

        def sign_out(api):
            api.sign_out(name)
            self._status_message = f"{name} signed out"
            self._check_auth_status()

        self._act(f"Signing {name} out...", sign_out)

    def _verify_code(self, api, code: str) -> None:
        """No code is typed here: the user types it at Microsoft's page."""

    def _refresh(self):
        self._status_message = "Refreshing..."
        self._check_auth_status_async()
