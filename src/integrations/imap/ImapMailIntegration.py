from typing import List

from assistant.integration.Accounts import AccountHealth, id_prefixes, named_entries
from assistant.integration.Integration import Integration
from assistant.integration.mail.ImapMailAccount import ImapMailAccount, PRESETS
from assistant.integration.mail.MailAccount import MailAccount
from assistant.integration.mail.MailSource import MailSource
from common.ConfigManager import ConfigManager
from common.logging import info, trace

SECTION = "imap"
ACCOUNTS_KEY = f"{SECTION}.accounts"


class ImapMailIntegration(AccountHealth, Integration, MailSource):
    """Mail over IMAP from iCloud, Gmail or any server: read, file and send.

    Reads, searches and files the mail of every account listed under imap.accounts and sends
    from the one the user means; iCloud and Gmail sign in with an app password, any other
    server with its IMAP and SMTP hosts.

    A MailSource building one ImapMailAccount per `imap.accounts[]` entry: `name` (unique, the
    source of the id prefix in listings), `preset` (icloud | gmail) or `imap_host` / `smtp_host`
    / `smtp_port` / `folder` / `sent_folder`, `address`, `password`. The credentials are read
    from config on every call, so a password set through config_set works without a rebuild;
    a changed list of accounts needs restart_integration. The health check tries every
    account and names the failing ones."""
    sensitive = True

    def __init__(self):
        Integration.__init__(self)
        self._accounts = self._build_accounts()

    @property
    def name(self) -> str:
        return "IMAP Mail"

    @property
    def config_keys(self) -> List[str]:
        return [ACCOUNTS_KEY]

    def mail_accounts(self) -> list[MailAccount]:
        return list(self._accounts)

    def check_health(self) -> bool:
        if not self._accounts:
            trace("IMAP Mail: no account to check")
            return False
        return self.check_accounts(self._accounts)

    def _health_accounts(self) -> list:
        return self._accounts

    @staticmethod
    def _entry(index: int) -> dict:
        """The `imap.accounts[index]` section as config.yaml holds it now, {} when gone."""
        entries = ConfigManager.get_instance().get(ACCOUNTS_KEY, [])
        if isinstance(entries, list) and index < len(entries) and isinstance(entries[index], dict):
            return entries[index]
        return {}

    @classmethod
    def _credentials(cls, index: int):
        entry = cls._entry(index)
        return str(entry.get("address") or ""), str(entry.get("password") or "")

    @classmethod
    def _build_accounts(cls) -> list[ImapMailAccount]:
        config = ConfigManager.get_instance()
        entries = named_entries(config.get(ACCOUNTS_KEY, []), SECTION)
        prefixes = id_prefixes([entry["name"] for _, entry in entries])
        accounts = []
        for (index, entry), prefix in zip(entries, prefixes):
            name = str(entry["name"]).strip()
            preset = str(entry.get("preset") or "").strip().lower()
            if preset and preset not in PRESETS:
                info(f"IMAP Mail: account '{name}' names an unknown preset '{preset}' (known: {', '.join(PRESETS)}), skipped")
                continue
            try:
                account = ImapMailAccount(
                    name, prefix, credentials=lambda index=index: cls._credentials(index), preset=preset,
                    imap_host=str(entry.get("imap_host") or ""), smtp_host=str(entry.get("smtp_host") or ""),
                    smtp_port=int(entry.get("smtp_port") or 0), folder=str(entry.get("folder") or ""),
                    sent_folder=entry.get("sent_folder"))
            except ValueError as e:
                info(f"IMAP Mail: account '{name}' skipped — {e}")
                continue
            trace(f"IMAP Mail: account '{name}' ({prefix}) on {account.imap_host}" + (f", preset {preset}" if preset else ""))
            accounts.append(account)
        info(f"IMAP Mail: {len(accounts)} account(s): {', '.join(a.name for a in accounts) or 'none'}")
        return accounts
