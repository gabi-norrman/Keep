"""The sockets' bind address follows `interfaces.lan_access`: loopback until the key is on."""
from unittest.mock import patch

from common.ConfigManager import config
from interfaces.Interface import bind_host, LAN_ACCESS_KEY, LOOPBACK, ALL_INTERFACES
from common import ConfigStore


def _config_with(value):
    real_get = config.get
    return patch.object(config, "get", side_effect=lambda key, *a, **k: value if key == LAN_ACCESS_KEY
                        else real_get(key, *a, **k))


def test_an_install_binds_loopback_until_the_key_is_set():
    with patch.object(config, "get", side_effect=lambda key, default=None, *a, **k: default):
        assert bind_host(config, "The HTTP server") == LOOPBACK


def test_lan_access_true_binds_every_interface_of_the_machine():
    with _config_with(True):
        assert bind_host(config, "The TUI console") == ALL_INTERFACES


def test_the_assistant_may_set_the_key_contract_with_config_yaml():
    """`interfaces.http` is denied to config_set, the lan key beside it is the one the assistant
    flips for the phone app — so it must stay outside the denied prefixes and in the example."""
    ConfigStore.check_key(LAN_ACCESS_KEY)
    assert ConfigStore.coerce(LAN_ACCESS_KEY, "true") is True
