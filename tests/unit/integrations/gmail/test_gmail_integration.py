"""The Gmail folder: a MailSource holding one account built from the gmail preset and the
`email` section."""
from unittest.mock import patch

import pytest

from assistant.integration.mail.MailSource import MailSource
from integrations.gmail.GmailIntegration import GmailIntegration


@pytest.fixture
def gmail():
    GmailIntegration.reset_instance()
    yield
    GmailIntegration.reset_instance()


def _config(values):
    config = patch('integrations.gmail.GmailIntegration.ConfigManager.get_instance').start().return_value
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    return config


@pytest.mark.unit
class TestGmailIntegration:
    def test_is_a_mail_source_with_one_gmail_preset_account(self, gmail):
        _config({"email.address": "marcus@example.com", "email.password": "app-pw"})
        try:
            integration = GmailIntegration()
        finally:
            patch.stopall()
        account, = integration.mail_accounts()
        assert isinstance(integration, MailSource)
        assert (account.id_prefix, account.preset, account.imap_host) == ("GM", "gmail", "imap.gmail.com")
        assert account.address == "marcus@example.com"
        assert integration.config_keys == ["email"]

    def test_the_credentials_are_a_contract_with_email_address_and_email_password(self, gmail):
        config = _config({"email.address": "marcus@example.com", "email.password": "app-pw"})
        try:
            account, = GmailIntegration().mail_accounts()
            assert account._credentials() == ("marcus@example.com", "app-pw")
            # Read at call time, so a password changed through config_set is used without a rebuild.
            config.get.side_effect = lambda key, default=None: {"email.address": "marcus@example.com", "email.password": "new-pw"}.get(key, default)
            assert account._credentials() == ("marcus@example.com", "new-pw")
        finally:
            patch.stopall()

    def test_the_smtp_server_is_a_contract_with_email_smtp_host_and_email_smtp_port(self, gmail):
        _config({"email.address": "marcus@example.com", "email.password": "app-pw",
                 "email.smtp_host": "smtp.mail.example", "email.smtp_port": "2525"})
        try:
            account, = GmailIntegration().mail_accounts()
        finally:
            patch.stopall()
        assert (account.smtp_host, account.smtp_port) == ("smtp.mail.example", 2525)

    def test_without_the_override_the_preset_smtp_server_is_used(self, gmail):
        _config({"email.address": "marcus@example.com", "email.password": "app-pw"})
        try:
            account, = GmailIntegration().mail_accounts()
        finally:
            patch.stopall()
        assert (account.smtp_host, account.smtp_port) == ("smtp.gmail.com", 587)

    def test_the_health_check_is_the_accounts(self, gmail):
        _config({"email.address": "marcus@example.com", "email.password": "app-pw"})
        try:
            integration = GmailIntegration()
        finally:
            patch.stopall()
        with patch.object(integration._account, "check", return_value=True) as check:
            assert integration.check_health() is True
        check.assert_called_once()
