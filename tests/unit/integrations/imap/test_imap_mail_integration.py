"""The IMAP Mail folder: a MailSource building one ImapMailAccount per `imap.accounts` entry,
from a preset or from the hosts the entry gives, with the id prefixes derived from the names
and a health check that names the failing accounts."""
from unittest.mock import MagicMock, patch

import pytest

from assistant.integration.mail.MailSource import MailSource
from integrations.imap.ImapMailIntegration import ImapMailIntegration

CONFIG = 'integrations.imap.ImapMailIntegration.ConfigManager.get_instance'


def _config(accounts):
    """ConfigManager.get as the integration reads it, serving `imap.accounts`."""
    config = MagicMock()
    config.get.side_effect = lambda key, *default: accounts if key == "imap.accounts" else (default[0] if default else None)
    return patch(CONFIG, return_value=config)


@pytest.fixture(autouse=True)
def fresh():
    ImapMailIntegration.reset_instance()
    yield
    ImapMailIntegration.reset_instance()


@pytest.mark.unit
class TestAccounts:
    def test_one_account_per_entry_a_preset_filling_the_hosts_a_contract_with_imap_accounts(self):
        entries = [{"name": "Personal", "preset": "icloud", "address": "m@mlab.test", "password": "app-pw"},
                   {"name": "Gmail", "preset": "gmail", "address": "marcus@example.com", "password": "pw", "smtp_port": "2525"},
                   {"name": "Work", "address": "marcus@company.example", "password": "pw",
                    "imap_host": "imap.company.example", "smtp_host": "smtp.company.example", "folder": "Inbox", "sent_folder": "Sent"}]
        with _config(entries):
            integration = ImapMailIntegration()
            personal, gmail, work = integration.mail_accounts()
            assert isinstance(integration, MailSource) and integration.config_keys == ["imap.accounts"]
            assert (personal.imap_host, personal.sent_folder, personal.address) == ("imap.mail.me.com", "Sent Messages", "m@mlab.test")
            assert (gmail.imap_host, gmail.default_folder, gmail.smtp_port) == ("imap.gmail.com", "[Gmail]/All Mail", 2525)
            assert (work.imap_host, work.smtp_host, work.default_folder, work.sent_folder) == (
                "imap.company.example", "smtp.company.example", "Inbox", "Sent")
            assert work.preset == "" and work._credentials() == ("marcus@company.example", "pw")

    def test_the_id_prefixes_come_from_the_names(self):
        entries = [{"name": "Personal", "preset": "icloud"}, {"name": "Pets Club", "preset": "gmail"}, {"name": "Work", "preset": "icloud"}]
        with _config(entries):
            assert [a.id_prefix for a in ImapMailIntegration().mail_accounts()] == ["PE", "PC", "WO"]

    def test_the_password_is_read_from_config_at_call_time_so_config_request_needs_no_rebuild(self):
        entries = [{"name": "Personal", "preset": "icloud", "address": "m@mlab.test", "password": ""}]
        with _config(entries):
            account, = ImapMailIntegration().mail_accounts()
            assert account._credentials() == ("m@mlab.test", "")
            entries[0]["password"] = "app-pw"
            assert account._credentials() == ("m@mlab.test", "app-pw")

    def test_an_entry_without_a_name_a_repeated_name_an_unknown_preset_or_no_hosts_is_skipped(self):
        entries = [{"preset": "icloud", "address": "a@example.com", "password": "x"},
                   {"name": "Personal", "preset": "icloud", "address": "m@mlab.test", "password": "x"},
                   {"name": "personal", "preset": "gmail", "address": "other@example.com", "password": "x"},
                   {"name": "Work", "preset": "exchange", "address": "w@example.com", "password": "x"},
                   {"name": "Bare", "address": "b@example.com", "password": "x"},
                   "not a section"]
        with _config(entries):
            accounts = ImapMailIntegration().mail_accounts()
            assert [(a.name, a.address) for a in accounts] == [("Personal", "m@mlab.test")]

    def test_no_list_at_all_is_no_account(self):
        with _config(None):
            assert ImapMailIntegration().mail_accounts() == []


@pytest.mark.unit
class TestHealth:
    def test_every_account_is_checked_and_the_failing_ones_are_named(self):
        entries = [{"name": "Personal", "preset": "icloud"}, {"name": "Work", "preset": "gmail"}]
        with _config(entries):
            integration = ImapMailIntegration()
        personal, work = integration.mail_accounts()
        with patch.object(personal, "check", side_effect=OSError("timed out")), patch.object(work, "check", return_value=True):
            with pytest.raises(RuntimeError) as raised:
                integration.check_health()
        assert "Personal" in str(raised.value) and "timed out" in str(raised.value) and "Work" not in str(raised.value)
        status = {a["name"]: a for a in integration.accounts_status()}
        assert status["Personal"]["state"] == "error" and "timed out" in status["Personal"]["last_error"]
        assert status["Work"]["state"] == "ok" and status["Work"]["last_error"] is None

    def test_before_the_first_check_every_account_is_unknown(self):
        with _config([{"name": "Personal", "preset": "icloud"}]):
            integration = ImapMailIntegration()
        assert [a["state"] for a in integration.accounts_status()] == ["unknown"]

    def test_all_passing_is_healthy(self):
        with _config([{"name": "Personal", "preset": "icloud"}]):
            integration = ImapMailIntegration()
        with patch.object(integration.mail_accounts()[0], "check", return_value=True):
            assert integration.check_health() is True

    def test_without_an_account_health_fails(self):
        with _config([]):
            assert ImapMailIntegration().check_health() is False
