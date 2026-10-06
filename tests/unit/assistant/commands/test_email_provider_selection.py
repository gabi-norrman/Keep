"""Which email provider the mail commands run against — the autouse fixture in
test_commands.py replaces _create_email_provider, so this lives in its own module."""
from unittest.mock import Mock, patch

from assistant.commands.Commands import Commands
class TestEmailProviderSelection:
    def test_mail_commands_get_the_setup_hint_instead_of_a_key_error(self):
        from assistant.integration.EmailProvider import NotConfiguredEmail
        config = Mock()
        config.get.return_value = ""
        with patch("assistant.commands.Commands.ConfigManager.get_instance", return_value=config):
            provider = Commands._create_email_provider()
        assert isinstance(provider, NotConfiguredEmail)
        assert provider.fetch_latest().startswith("Email is not configured")
        assert "list_integrations" in provider.search("x")
        assert provider.subject_for("GM-AA") is None

    def test_configured_accounts_are_the_monitored_singletons(self):
        from integrations.gmail.GmailIntegration import GmailAccess
        config = Mock()
        config.get.side_effect = lambda key, default=None: "me@gmail.com" if key == "email.address" else default
        with patch("assistant.commands.Commands.ConfigManager.get_instance", return_value=config), \
             patch.object(GmailAccess, "get_instance", return_value=Mock(spec=GmailAccess)) as get_instance:
            assert Commands._create_email_provider() is get_instance.return_value
