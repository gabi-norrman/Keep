"""FirstRun: an install with fewer than three integrations is in the FTUE — greeted by the
assistant, with the setup toolkit preloaded."""
from unittest.mock import patch

import pytest

from assistant import FirstRun
from common.ConfigManager import config


def _config(overrides: dict):
    real_get = config.get
    return patch.object(config, 'get', side_effect=lambda key, default=None: overrides.get(key, real_get(key, default)))


@pytest.mark.unit
class TestInFtue:
    def test_no_enabled_integration_is_the_ftue(self):
        with _config({'enabled_integrations': []}):
            assert FirstRun.in_ftue() is True

    def test_two_enabled_integrations_still_are(self):
        with _config({'enabled_integrations': ['iMessage', 'iCloud']}):
            assert FirstRun.in_ftue() is True

    def test_three_enabled_integrations_end_it(self):
        assert FirstRun.FTUE_INTEGRATION_THRESHOLD == 3
        with _config({'enabled_integrations': ['iMessage', 'iCloud', 'Photos']}):
            assert FirstRun.in_ftue() is False

    def test_the_setup_toolkit_is_preloaded_only_in_the_ftue(self):
        from assistant.commands import Toolkits
        with _config({'enabled_integrations': ['Telegram']}):
            assert FirstRun.preload_toolkits() == ("setup",)
        with _config({'enabled_integrations': ['iMessage', 'iCloud', 'Photos']}):
            assert FirstRun.preload_toolkits() == ()
        assert Toolkits.by_name("setup") is not None


@pytest.mark.unit
class TestBrief:
    def test_names_the_owner_when_known(self):
        with _config({'owner_name': 'Anna'}):
            text = FirstRun.brief()
        assert "You are talking to Anna." in text
        assert "owner_name is not set" not in text

    def test_asks_for_the_owner_name_when_not_set(self):
        with _config({'owner_name': 'user'}):
            text = FirstRun.brief()
        assert 'config_set("owner_name", ...)' in text
        assert "You are talking to" not in text

    def test_the_setup_order(self):
        with _config({'owner_name': 'Anna'}):
            text = FirstRun.brief()
        assert text.index("1. iMessage") < text.index("2. iCloud") < text.index("3. Photos")
