from typing import Callable, List, Optional

from assistant.integration.Accounts import AccountHealth, STATE_ERROR, id_prefixes, named_entries
from assistant.integration.Integration import Integration
from assistant.integration.calendar.CalendarAccount import CalendarAccount, split_names
from assistant.integration.calendar.CalendarSource import CalendarSource
from assistant.integration.mail.MailAccount import MailAccount
from assistant.integration.mail.MailSource import MailSource
from common.ConfigManager import ConfigManager
from common.logging import info, trace
from integrations.microsoft.GraphCalendarAccount import GraphCalendarAccount
from integrations.microsoft.GraphClient import GraphClient
from integrations.microsoft.GraphMailAccount import GraphMailAccount
from integrations.microsoft.MicrosoftAuth import (DEFAULT_TENANT, DeviceFlow, MicrosoftAuth, NotSignedIn,
                                                  delete_all_token_files, not_signed_in_hint)

SECTION = "microsoft"
ACCOUNTS_KEY = f"{SECTION}.accounts"
CLIENT_ID_KEY = f"{SECTION}.app.client_id"
TENANT_KEY = f"{SECTION}.app.tenant"
DEFAULT_CALENDAR = "Calendar"


class MicrosoftAccount:
    """One signed-in (or not yet) Microsoft 365 account: its auth, and the mail and calendar
    accounts it gives when the entry asks for them. `check()` is the per-account health:
    a token, then a Graph call."""

    def __init__(self, name: str, address: Callable[[], str], auth: MicrosoftAuth, client: GraphClient,
                 mail: Optional[GraphMailAccount], calendar: Optional[GraphCalendarAccount]):
        self.name = name
        self._address = address
        self.auth = auth
        self.client = client
        self.mail = mail
        self.calendar = calendar

    @property
    def address(self) -> str:
        return self._address() or ""

    def signed_in(self) -> bool:
        return self.auth.signed_in()

    def check(self) -> bool:
        if not self.auth.signed_in():
            raise NotSignedIn(not_signed_in_hint(self.name))
        me = self.client.get("/me", {"$select": "userPrincipalName,mail"})
        trace(f"{self.name}: Graph answers as {me.get('mail') or me.get('userPrincipalName') or 'an account'}")
        return True


class MicrosoftIntegration(AccountHealth, Integration, MailSource, CalendarSource):
    """Microsoft 365 mail and calendars over Graph: read, send and add events.

    Reads, searches and files the mail of every Microsoft account listed under
    microsoft.accounts, sends from the one the user means, and shows and adds to their
    calendars; an account signs in once with a code typed at microsoft.com/devicelogin.

    A MailSource and CalendarSource building one MicrosoftAccount per `microsoft.accounts[]`
    entry (`name`, `address`, `mail`, `calendar`, `default_calendar`, `shown_calendars`) over
    one app registration (`microsoft.app.client_id` / `tenant`). The accounts given to the
    composites are those with the flag AND a token in `cache/oauth/microsoft-<slug>.json`
    (MicrosoftAuth); one without a token is left out and reported in `accounts_status` and the
    health error with the microsoft_login hint. Config is read at call time; a changed list of
    accounts needs restart_integration. Losing the config deletes the token files."""
    sensitive = True

    def __init__(self):
        Integration.__init__(self)
        self._accounts = self._build_accounts()

    @property
    def name(self) -> str:
        return "Microsoft 365"

    @property
    def config_keys(self) -> List[str]:
        return [CLIENT_ID_KEY, ACCOUNTS_KEY]

    # --- what the composites, the command and the tab read -----------------------------------

    def accounts(self) -> list[MicrosoftAccount]:
        return list(self._accounts)

    def account(self, name: str) -> Optional[MicrosoftAccount]:
        wanted = name.strip().lower()
        return next((a for a in self._accounts if a.name.lower() == wanted), None)

    def mail_accounts(self) -> list[MailAccount]:
        return [a.mail for a in self._accounts if a.mail is not None and self._usable(a)]

    def calendar_accounts(self) -> list[CalendarAccount]:
        return [a.calendar for a in self._accounts if a.calendar is not None and self._usable(a)]

    @staticmethod
    def _usable(account: MicrosoftAccount) -> bool:
        if account.signed_in():
            return True
        info(f"Microsoft 365: account '{account.name}' is {not_signed_in_hint(account.name)}, left out")
        return False

    def login_begin(self, name: str) -> DeviceFlow:
        account = self.account(name)
        if account is None:
            raise ValueError(f"no Microsoft account named '{name}' (configured: {', '.join(a.name for a in self._accounts) or 'none'})")
        return account.auth.begin()

    def login_finish(self, name: str, flow: DeviceFlow) -> str:
        account = self.account(name)
        if account is None:
            return f"no Microsoft account named '{name}'"
        return account.auth.finish(flow)

    def sign_out(self, name: str) -> None:
        account = self.account(name)
        if account is not None:
            account.auth.sign_out()

    def get_auth_status(self) -> dict:
        """The console tab's summary of the accounts' sign-in state."""
        if not self._accounts:
            return {"status": "error", "message": "no account under microsoft.accounts"}
        client_id, _ = self._app_settings()
        if not client_id:
            return {"status": "error", "message": "microsoft.app.client_id is not set"}
        missing = [a.name for a in self._accounts if not a.signed_in()]
        if missing:
            return {"status": "needs_auth", "message": f"Not signed in: {', '.join(missing)} — Ctrl-L signs the selected account in"}
        return {"status": "authenticated", "message": f"{len(self._accounts)} account(s) signed in"}

    # --- health ------------------------------------------------------------------------------

    def check_health(self) -> bool:
        if not self._accounts:
            trace("Microsoft 365: no account to check")
            return False
        return self.check_accounts(self._accounts)

    def _health_accounts(self) -> list:
        return self._accounts

    def accounts_status(self) -> list[dict]:
        status = super().accounts_status()
        by_name = {a.name: a for a in self._accounts}
        for entry in status:
            if not by_name[entry["name"]].signed_in():
                entry["state"], entry["last_error"] = STATE_ERROR, not_signed_in_hint(entry["name"])
        return status

    def on_not_configured(self):
        delete_all_token_files()

    # --- building ----------------------------------------------------------------------------

    @staticmethod
    def _app_settings() -> tuple[str, str]:
        config = ConfigManager.get_instance()
        return str(config.get(CLIENT_ID_KEY, "") or ""), str(config.get(TENANT_KEY, "") or DEFAULT_TENANT)

    @staticmethod
    def _entry(index: int) -> dict:
        """The `microsoft.accounts[index]` section as config.yaml holds it now, {} when gone."""
        entries = ConfigManager.get_instance().get(ACCOUNTS_KEY, [])
        if isinstance(entries, list) and index < len(entries) and isinstance(entries[index], dict):
            return entries[index]
        return {}

    @staticmethod
    def _flag(entry: dict, key: str) -> bool:
        value = entry.get(key, True)
        return str(value).strip().lower() not in ("false", "no", "0", "off") if value is not None else True

    @classmethod
    def _build_accounts(cls) -> list[MicrosoftAccount]:
        config = ConfigManager.get_instance()
        entries = named_entries(config.get(ACCOUNTS_KEY, []), SECTION)
        prefixes = id_prefixes([entry["name"] for _, entry in entries])
        accounts = []
        for (index, entry), prefix in zip(entries, prefixes):
            name = str(entry["name"]).strip()
            mail, calendar = cls._flag(entry, "mail"), cls._flag(entry, "calendar")
            if not mail and not calendar:
                info(f"Microsoft 365: account '{name}' has neither mail nor calendar on, skipped")
                continue
            auth = MicrosoftAuth(name, cls._app_settings)
            client = GraphClient(auth)
            address = lambda i=index: str(cls._entry(i).get("address") or "")
            mail_account = GraphMailAccount(name, prefix, address, client) if mail else None
            calendar_account = GraphCalendarAccount(
                name, client,
                shown_calendars=lambda i=index: split_names(cls._entry(i).get("shown_calendars", [])),
                default_calendar=lambda i=index: str(cls._entry(i).get("default_calendar") or DEFAULT_CALENDAR),
                timezone=lambda: ConfigManager.get_instance().get_home_timezone()) if calendar else None
            account = MicrosoftAccount(name, address, auth, client, mail_account, calendar_account)
            trace(f"Microsoft 365: account '{name}' ({prefix}) mail={mail} calendar={calendar} "
                  f"{'signed in' if account.signed_in() else 'not signed in'}")
            accounts.append(account)
        info(f"Microsoft 365: {len(accounts)} account(s): {', '.join(a.name for a in accounts) or 'none'}")
        return accounts
