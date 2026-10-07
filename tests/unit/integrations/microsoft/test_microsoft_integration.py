"""The Microsoft 365 folder: one account per `microsoft.accounts` entry, the mail and calendar
accounts given to the composites only when the entry asks for them AND a token exists, and
the account without a token named in the status and the health error."""
import json
from unittest.mock import MagicMock, patch

import pytest

from assistant.integration.calendar.CalendarSource import CalendarSource
from assistant.integration.mail.MailSource import MailSource
from integrations.microsoft import MicrosoftAuth as auth_module
from integrations.microsoft.MicrosoftAuth import token_path
from integrations.microsoft.MicrosoftIntegration import MicrosoftIntegration

CONFIG = 'integrations.microsoft.MicrosoftIntegration.ConfigManager.get_instance'
ENTRIES = [{"name": "Work", "address": "marcus@company.example", "mail": True, "calendar": True, "default_calendar": "Calendar"},
           {"name": "Family", "address": "marcus@example.com", "mail": True, "calendar": False},
           {"name": "Club", "address": "club@example.com", "mail": "false", "calendar": True, "shown_calendars": ["Matches"]}]


def _config(accounts, client_id="client-1"):
    values = {"microsoft.accounts": accounts, "microsoft.app.client_id": client_id, "microsoft.app.tenant": "common"}
    config = MagicMock()
    config.get.side_effect = lambda key, *default: values.get(key, default[0] if default else None)
    config.get_home_timezone.return_value = "Europe/Stockholm"
    return patch(CONFIG, return_value=config)


def _signed_in(token_dir, name, username):
    token_dir.mkdir(parents=True, exist_ok=True)
    token_path(name).write_text(json.dumps({"Account": {"k": {"home_account_id": "u.t", "environment": "login.microsoftonline.com",
                                                              "realm": "common", "local_account_id": "u", "username": username,
                                                              "authority_type": "MSSTS"}}}))


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    MicrosoftIntegration.reset_instance()
    with patch.object(auth_module, "TOKEN_DIR", tmp_path / "oauth"):
        yield tmp_path / "oauth"
    MicrosoftIntegration.reset_instance()


@pytest.mark.unit
class TestAccounts:
    def test_only_a_flagged_account_with_a_token_reaches_the_composites_a_contract_with_microsoft_accounts(self, fresh):
        _signed_in(fresh, "Work", "marcus@company.example")
        _signed_in(fresh, "Club", "club@example.com")
        with _config(ENTRIES):
            integration = MicrosoftIntegration()
            assert isinstance(integration, MailSource) and isinstance(integration, CalendarSource)
            assert integration.config_keys == ["microsoft.app.client_id", "microsoft.accounts"]
            assert [a.name for a in integration.accounts()] == ["Work", "Family", "Club"]
            assert [a.name for a in integration.mail_accounts()] == ["Work"]          # Family has no token, Club no mail
            assert [a.name for a in integration.calendar_accounts()] == ["Work", "Club"]
            work, = integration.mail_accounts()
            assert (work.id_prefix, work.address) == ("WO", "marcus@company.example")
            club = integration.calendar_accounts()[1]
            assert (club.shown_calendars, club.default_calendar, integration.calendar_accounts()[0].default_calendar) == (
                ["Matches"], "Calendar", "Calendar")

    def test_an_account_with_neither_mail_nor_calendar_and_a_repeated_name_are_skipped(self, fresh):
        entries = [{"name": "Work", "mail": False, "calendar": False}, {"name": "Family"}, {"name": "family", "mail": True}]
        with _config(entries):
            assert [a.name for a in MicrosoftIntegration().accounts()] == ["Family"]

    def test_the_address_is_read_from_config_at_call_time(self, fresh):
        _signed_in(fresh, "Work", "marcus@company.example")
        entries = [{"name": "Work", "address": ""}]
        with _config(entries):
            account, = MicrosoftIntegration().mail_accounts()
            assert account.address == ""
            entries[0]["address"] = "marcus@company.example"
            assert account.address == "marcus@company.example"

    def test_a_signed_in_account_is_found_by_name_whatever_the_case(self, fresh):
        with _config(ENTRIES):
            integration = MicrosoftIntegration()
            assert integration.account("work").name == "Work" and integration.account("Nobody") is None
            with pytest.raises(ValueError) as raised:
                integration.login_begin("Nobody")
            assert "Work" in str(raised.value)


@pytest.mark.unit
class TestStatus:
    def test_the_account_without_a_token_is_reported_with_the_sign_in_hint_before_any_check(self, fresh):
        _signed_in(fresh, "Work", "marcus@company.example")
        with _config(ENTRIES):
            integration = MicrosoftIntegration()
            status = {a["name"]: a for a in integration.accounts_status()}
            assert status["Work"]["state"] == "unknown"
            assert status["Family"]["state"] == "error" and status["Family"]["last_error"] == 'not signed in — microsoft_login("Family")'
            assert integration.get_auth_status()["status"] == "needs_auth" and "Family" in integration.get_auth_status()["message"]

    def test_the_health_check_names_the_account_without_a_token_and_passes_the_signed_in_one(self, fresh):
        _signed_in(fresh, "Work", "marcus@company.example")
        with _config(ENTRIES[:2]):
            integration = MicrosoftIntegration()
        work, family = integration.accounts()
        with patch.object(work.client, "get", return_value={"mail": "marcus@company.example"}):
            with pytest.raises(RuntimeError) as raised:
                integration.check_health()
        assert "Family" in str(raised.value) and "microsoft_login" in str(raised.value) and "Work:" not in str(raised.value)
        assert {a["name"]: a["state"] for a in integration.accounts_status()} == {"Work": "ok", "Family": "error"}

    def test_everyone_signed_in_is_healthy_and_authenticated(self, fresh):
        _signed_in(fresh, "Work", "marcus@company.example")
        with _config(ENTRIES[:1]):
            integration = MicrosoftIntegration()
            with patch.object(integration.accounts()[0].client, "get", return_value={}):
                assert integration.check_health() is True
            assert integration.get_auth_status()["status"] == "authenticated"

    def test_without_an_account_or_a_client_id_nothing_is_healthy(self, fresh):
        with _config([]):
            integration = MicrosoftIntegration()
            assert integration.check_health() is False and integration.get_auth_status()["status"] == "error"
        MicrosoftIntegration.reset_instance()
        with _config(ENTRIES[:1], client_id=""):
            assert "client_id" in MicrosoftIntegration().get_auth_status()["message"]

    def test_losing_the_config_deletes_every_token_file(self, fresh):
        _signed_in(fresh, "Work", "marcus@company.example")
        _signed_in(fresh, "Family", "marcus@example.com")
        with _config(ENTRIES):
            MicrosoftIntegration().on_not_configured()
        assert list(fresh.iterdir()) == []
