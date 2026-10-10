from datetime import datetime, timezone
from typing import List, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.tabs.AuthTab import AuthTab, BACKSPACE_KEYS, ENTER_KEYS, PENDING
from interfaces.console.widgets.Board import Fact, SummaryTile, Table
from common.logging import info, trace
from integrations.google.GoogleIntegration import GoogleIntegration

NAME_WIDTH, ADDRESS_WIDTH, GIVES_WIDTH, STATE_WIDTH = 16, 30, 16, 14
ENTRY_TAIL = 60             # how much of the pasted address the entry row shows


class GoogleTab(AuthTab):
    """The Google accounts: which are signed in, and the browser sign-in of the selected one —
    the URL to open, then the address the browser lands on pasted back — or its sign-out."""

    integration = GoogleIntegration

    HELP = "Ctrl-R refresh  Ctrl-L sign in  Ctrl-E paste the address  Ctrl-O sign out  ↑↓ select account"
    SUBMITTING_MESSAGE = "Signing in with the pasted address..."
    STATUS_LABELS = {
        **AuthTab.STATUS_LABELS,
        "needs_auth": ("Sign-in needed", Theme.COLOR_YELLOW),
    }

    def __init__(self):
        super().__init__()
        self._accounts: list = []
        self._selected = 0
        self._sign_in = None            # the SignIn whose URL the user is working through
        self._sign_in_account = ""

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
            trace(f"Google tab: no expiry for {account.name}: {e}")
            return PENDING
        if expires is None:
            return PENDING
        left = (expires - datetime.now(timezone.utc)).total_seconds()
        return "refresh due" if left <= 0 else f"token {int(left // 60)} min"

    @staticmethod
    def _state(account) -> Tuple[str, Color]:
        if not account.signed_in():
            return "not signed in", Theme.COLOR_YELLOW
        if account.auth.needs_sign_in_again():
            return "sign in again", Theme.COLOR_RED
        return "signed in", Theme.COLOR_GREEN

    def _table(self) -> Table:
        rows = []
        for i, account in enumerate(self._accounts):
            state, color = self._state(account)
            gives = " + ".join(part for part, on in (("mail", account.mail), ("calendar", account.calendar)) if on)
            marker = "▸ " if i == self._selected else "  "
            rows.append((color, [
                (marker + account.name, Theme.COLOR_BRIGHT if i == self._selected else Theme.COLOR_TEXT),
                (account.address or PENDING, Theme.COLOR_LABEL),
                (gives, Theme.COLOR_TEAL),
                (state, color),
                (self._expiry(account) if account.signed_in() else "", Theme.COLOR_DIM)]))
        return Table("Accounts",
                     [("Account", NAME_WIDTH), ("Address", ADDRESS_WIDTH), ("Gives", GIVES_WIDTH), ("State", STATE_WIDTH), ("Token", None)],
                     rows, "no account under google.accounts")

    def _tiles(self) -> List[SummaryTile]:
        signed_in = sum(1 for a in self._accounts if a.signed_in() and not a.auth.needs_sign_in_again())
        if not self._accounts:
            return [("Accounts", PENDING, Theme.COLOR_DIM, [("none configured", Theme.COLOR_DIM)])]
        color = Theme.COLOR_GREEN if signed_in == len(self._accounts) else Theme.COLOR_YELLOW
        return [("Accounts", f"{signed_in}/{len(self._accounts)}", color, [("signed in", Theme.COLOR_LABEL)])]

    def _fields(self) -> List[Fact]:
        account = self._selected_account()
        name = account.name if account else PENDING
        if self._input_mode:
            shown = self._input_text if len(self._input_text) <= ENTRY_TAIL else "…" + self._input_text[-ENTRY_TAIL:]
            return [Fact("Account", self._sign_in_account or name, Theme.COLOR_TEXT),
                    Fact("Address", shown or "paste the address the browser landed on", Theme.COLOR_BRIGHT, waiting=True, bold=True)]
        if self._sign_in is not None:
            rows = self._link_rows(self._sign_in.url)
            return ([Fact("Account", self._sign_in_account, Theme.COLOR_TEXT)]
                    + [Fact("Open" if i == 0 else "", row, Theme.COLOR_BRIGHT, bold=True) for i, row in enumerate(rows)]
                    + [Fact("Then", "Ctrl-E, paste the address the browser lands on, Enter", Theme.COLOR_YELLOW, waiting=True)])
        hint = self._entry_hint()
        return [Fact("Account", name, Theme.COLOR_TEXT),
                Fact("Sign-in", hint[0] if hint else "no account", hint[1] if hint else Theme.COLOR_FAINT)]

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        account = self._selected_account()
        if account is None:
            return None
        if account.signed_in() and not account.auth.needs_sign_in_again():
            return "signed in — Ctrl-L signs in again, Ctrl-O signs out", Theme.COLOR_GREEN
        return "Ctrl-L shows the sign-in link", Theme.COLOR_YELLOW

    # --- keys --------------------------------------------------------------------------------

    def handle_input(self, key: str) -> bool:
        if not self._input_mode and key in ('up', 'down') and self._accounts:
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
        account = self._selected_account()
        if account is None:
            self._status_message = "No account to sign in"
            return
        name = account.name

        def sign_in(api):
            self._sign_in, self._sign_in_account = api.login_begin(name), name
            self._status_message = f"Waiting — open the link, sign in as {account.address or name}, then Ctrl-E and paste the address"
            info(f"Google tab: sign-in link for {name}: {self._sign_in.url}")

        self._act(f"Signing {name} in...", sign_in)

    def _verify_code(self, api, code: str) -> None:
        name = self._sign_in_account or (self._selected_account().name if self._selected_account() else "")
        failure = api.login_finish(code, name)
        if failure:
            self._status_message = f"Sign-in of {name} failed: {failure}"
            info(f"Google tab: sign-in of {name} failed: {failure}")
        else:
            self._sign_in, self._sign_in_account = None, ""
            self._status_message = f"{name} signed in successfully"
            self._check_health_now()
        self._check_auth_status()

    def _sign_out(self):
        account = self._selected_account()
        if account is None:
            return
        name = account.name

        def sign_out(api):
            api.sign_out(name)
            if self._sign_in_account == name:
                self._sign_in, self._sign_in_account = None, ""
            self._status_message = f"{name} signed out"
            self._check_auth_status()

        self._act(f"Signing {name} out...", sign_out)

    def _refresh(self):
        self._status_message = "Refreshing..."
        self._check_auth_status_async()
