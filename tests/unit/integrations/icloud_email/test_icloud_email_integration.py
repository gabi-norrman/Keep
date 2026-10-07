"""The iCloud Email folder: a MailSource holding one account built from the icloud preset and
the `icloud_email` section."""
from unittest.mock import patch

import pytest

from assistant.integration.mail.MailSource import MailSource
from integrations.icloud_email.ICloudEmailIntegration import ICloudEmailIntegration


@pytest.fixture
def icloud():
    ICloudEmailIntegration.reset_instance()
    yield
    ICloudEmailIntegration.reset_instance()


@pytest.mark.unit
class TestICloudEmailIntegration:
    def test_is_a_mail_source_with_one_icloud_preset_account(self, icloud):
        with patch('integrations.icloud_email.ICloudEmailIntegration.ConfigManager.get_instance') as get_instance:
            get_instance.return_value.get.side_effect = lambda key, default=None: {
                "icloud_email.address": "m@mlab.test", "icloud_email.password": "app-pw"}.get(key, default)
            integration = ICloudEmailIntegration()
            account, = integration.mail_accounts()
            assert isinstance(integration, MailSource)
            assert (account.id_prefix, account.preset, account.sent_folder) == ("IC", "icloud", "Sent Messages")
            # The credentials are a contract with icloud_email.address / icloud_email.password.
            assert account._credentials() == ("m@mlab.test", "app-pw")
        assert integration.config_keys == ["icloud_email"]

    def test_the_health_check_is_the_accounts(self, icloud):
        with patch('integrations.icloud_email.ICloudEmailIntegration.ConfigManager.get_instance'):
            integration = ICloudEmailIntegration()
        with patch.object(integration._account, "check", side_effect=ConnectionError("down")):
            with pytest.raises(ConnectionError):
                integration.check_health()
