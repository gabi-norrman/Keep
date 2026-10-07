"""The one mailbox the commands and agents use: every mail account of every enabled and configured
integration, presented as one.

Listings, searches and folder lists fan out to all accounts in parallel, one
`--- Name (address) ---` block per account when there are several (a single account answers
bare, as it always did); per-email operations are routed by the id prefix (PE-/WO-) each account
stamps on its short ids. `sender` is the account a mail goes out from: the one named, else
`mail.send_from`, else the first. No account at all answers every operation with the setup hint,
so the assistant offers to set one up instead of failing on a missing singleton."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from typing import List, Optional

from common.ConfigManager import ConfigManager
from common.logging import error, trace, info
from .MailAccount import MailAccount
from .MailSource import MailSource

HINT = ("Email is not configured. Add an account under imap.accounts (name, preset icloud or gmail, address, "
        "password) — see Setting things up: list_integrations, config_help(\"imap\"), config_request.")
# The account send_email sends from unless the LLM names one; the first account when unset.
SEND_FROM_KEY = "mail.send_from"


class MailAccess:
    name = "Email"

    def __init__(self, accounts: list[MailAccount]):
        self._accounts = list(accounts)
        self._prefix_map = {a.id_prefix: a for a in self._accounts}

    @classmethod
    def current(cls) -> "MailAccess":
        """The accounts of every registered MailSource whose config keys are set — resolved per
        call, so a restarted or newly configured integration is what the next command uses."""
        from assistant.integration.IntegrationMonitor import IntegrationMonitor
        accounts: list[MailAccount] = []
        for integration in IntegrationMonitor.get_instance().of_type(MailSource):
            missing = integration.missing_config_keys()
            if missing:
                trace(f"Mail: {integration.name} is enabled but not configured ({', '.join(missing)}), no accounts from it")
                continue
            for account in integration.mail_accounts():
                clash = next((a for a in accounts if a.name.lower() == account.name.lower() or a.id_prefix == account.id_prefix), None)
                if clash is not None:
                    info(f"Mail: {integration.name}'s account '{account.name}' ({account.id_prefix}) clashes with "
                         f"'{clash.name}' ({clash.id_prefix}) of another integration, skipped — rename it")
                    continue
                accounts.append(account)
        if not accounts:
            trace("No email account configured — mail commands answer with the setup hint")
        return cls(accounts)

    @property
    def accounts(self) -> list[MailAccount]:
        return list(self._accounts)

    def account_for(self, address: str) -> Optional[MailAccount]:
        """The account whose own address is `address`, or None."""
        wanted = (address or "").strip().lower()
        for account in self._accounts:
            if account.address.strip().lower() == wanted:
                return account
        info(f"Mail: no account with the address {address!r} among {[a.name for a in self._accounts]}")
        return None

    def named(self, name: str) -> Optional[MailAccount]:
        """The account called *name* (case does not matter), or None."""
        wanted = (name or "").strip().strip("'\"").lower()
        return next((a for a in self._accounts if a.name.lower() == wanted), None) if wanted else None

    def sender(self, name: str = "") -> Optional[MailAccount]:
        """The account `send_email` sends from: the one named, else `mail.send_from`, else the
        first; None without accounts."""
        if name:
            account = self.named(name)
            if account is not None:
                trace(f"Mail: sending from {account.name}, as named")
                return account
            info(f"Mail: no account named {name!r}, sending from the default one")
        configured = str(ConfigManager.get_instance().get(SEND_FROM_KEY, "") or "")
        if configured:
            account = self.named(configured)
            if account is not None:
                trace(f"Mail: sending from {account.name} ({SEND_FROM_KEY})")
                return account
            info(f"Mail: {SEND_FROM_KEY} names '{configured}', which is not a configured account — sending from the first")
        chosen = self._accounts[0] if self._accounts else None
        if chosen is not None:
            trace(f"Mail: sending from {chosen.name}, the first account")
        return chosen

    def send(self, to: str, subject: str, body: str, account_name: str = "") -> str:
        account = self.sender(account_name)
        if account is None:
            info("Email send skipped: no mail account configured")
            return HINT
        return account.send(to, subject, body)

    def send_message(self, msg: Message, recipients: List[str]) -> str:
        account = self.sender()
        if account is None:
            info("Email send skipped: no mail account configured")
            return HINT
        return account.send_message(msg, recipients)

    def fetch_latest(self, folder=None, unread_only=False) -> str:
        return self._fan_out("fetch_latest", folder, unread_only)

    def search(self, search_string) -> str:
        return self._fan_out("search", search_string)

    def list_folders(self, params=None) -> str:
        return self._fan_out("list_folders", params)

    def read(self, email_id) -> str:
        if not self._accounts:
            return HINT
        email_id = str(email_id).strip()
        account = self._route(email_id)
        if account is not None:
            return account.read(email_id.split("-", 1)[1])
        if len(self._accounts) == 1:
            return self._accounts[0].read(email_id)
        for account in self._accounts:
            result = account.read(email_id)
            if "not found" not in result.lower():
                return result
        return f"Email with ID {email_id} not found in any provider."

    def move(self, email_id, dest_folder) -> str:
        if not self._accounts:
            return HINT
        email_id = str(email_id).strip()
        account = self._route(email_id)
        if account is not None:
            return account.move(email_id.split("-", 1)[1], dest_folder)
        if len(self._accounts) == 1:
            return self._accounts[0].move(email_id, dest_folder)
        return (f"Could not route email id '{email_id}' to a provider — "
                f"use the prefixed id from the listing (e.g. {self._accounts[0].id_prefix}-AB).")

    def subject_for(self, email_id) -> Optional[str]:
        if not self._accounts:
            return None
        email_id = str(email_id).strip()
        account = self._route(email_id)
        if account is not None:
            return account.subject_for(email_id.split("-", 1)[1])
        if len(self._accounts) == 1:
            return self._accounts[0].subject_for(email_id)
        return None

    def _route(self, email_id: str) -> Optional[MailAccount]:
        """The account whose prefix a `PE-AB` id carries, None for a bare or unknown one."""
        if "-" not in email_id:
            return None
        return self._prefix_map.get(email_id.split("-", 1)[0])

    def _fan_out(self, method_name: str, *args) -> str:
        if not self._accounts:
            return HINT
        if len(self._accounts) == 1:
            return getattr(self._accounts[0], method_name)(*args)
        results = {}
        with ThreadPoolExecutor(max_workers=len(self._accounts)) as executor:
            futures = {executor.submit(getattr(a, method_name), *args): a for a in self._accounts}
            for future, account in futures.items():
                try:
                    results[account.name] = future.result()
                except Exception as e:
                    error(f"Error fetching from {account.name}: {e}")
                    results[account.name] = f"Error fetching from {account.name}: {e}"
        return "\n\n".join(f"--- {self._label(a)} ---\n{results[a.name]}" for a in self._accounts)

    @staticmethod
    def _label(account: MailAccount) -> str:
        return f"{account.name} ({account.address})" if account.address else account.name
