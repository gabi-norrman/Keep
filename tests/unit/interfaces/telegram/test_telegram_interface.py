"""TelegramInterface wires the bot transport onto the ChatHub, installs the hub as the
Notifier and registers the transport with the IntegrationMonitor by class, so the bot can be
brought up at runtime once a token is stored (restart_integration("Telegram"))."""
from unittest.mock import MagicMock, patch

import pytest

from assistant.integration.Integration import IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from assistant.Notifier import NullNotifier, notifier, set_notifier
from interfaces.chat.ChatHub import ChatHub
from interfaces.telegram.TelegramInterface import TelegramInterface
from interfaces.telegram.TelegramTransport import TelegramTransport

TRANSPORT = "interfaces.telegram.TelegramTransport"


@pytest.fixture(autouse=True)
def clean_singletons():
    ChatHub.reset_instance()
    IntegrationMonitor.reset_instance()
    TelegramTransport.reset_instance()
    yield
    ChatHub.reset_instance()
    IntegrationMonitor.reset_instance()
    TelegramTransport.reset_instance()
    set_notifier(NullNotifier())


def _config(enabled=True, token="t"):
    values = {"interfaces.telegram.enabled": enabled, "telegram_bot.bot_token": token,
              "telegram_bot.allowed_chat_ids": [], "telegram_bot.notify_chat_ids": [], "telegram_bot.link_previews": False}

    def get(key, default=None):
        if key in values and values[key] not in (None, ""):
            return values[key]
        if default is not None:
            return default
        raise KeyError(key)
    return MagicMock(get=get)


@pytest.mark.unit
class TestEnabled:
    def test_the_flag_alone_decides_a_missing_token_is_a_not_configured_bot(self):
        assert TelegramInterface.enabled(_config()) is True
        assert TelegramInterface.enabled(_config(enabled=False)) is False
        assert TelegramInterface.enabled(_config(token="")) is True

    def test_the_transport_follows_the_interface_flag_not_enabled_integrations(self):
        assert TelegramTransport.enabled(_config()) is True
        assert TelegramTransport.enabled(_config(enabled=False)) is False


@pytest.mark.unit
def test_start_with_a_token_registers_the_transport_the_notifier_and_the_monitor():
    assistant = MagicMock()
    interface = TelegramInterface()
    config = _config()
    IntegrationMonitor.get_instance()._config_manager = config
    with patch(f"{TRANSPORT}.config", config), patch(f"{TRANSPORT}.TelegramTransport.start") as start:
        interface.start(assistant, app=None)

        hub = ChatHub.get_instance()
        transport = interface.transport
        assert isinstance(transport, TelegramTransport)
        assert hub.assistant is assistant
        assert hub.transports == [transport]
        assert notifier() is hub
        start.assert_called_once_with(hub)
        status = IntegrationMonitor.get_instance().get_all_statuses()
        assert [(s["name"], s["restartable"]) for s in status] == [("Telegram Chat", True)]

        with patch(f"{TRANSPORT}.TelegramTransport.stop") as stop:
            interface.stop()
        assert hub.transports == []
        stop.assert_called_once()


@pytest.mark.unit
def test_start_without_a_token_lists_the_bot_as_not_configured_and_keeps_it_off_the_hub():
    interface = TelegramInterface()
    config = _config(token="")
    IntegrationMonitor.get_instance()._config_manager = config
    with patch(f"{TRANSPORT}.config", config), patch(f"{TRANSPORT}.TelegramTransport.start") as start:
        interface.start(MagicMock(), app=None)

    hub = ChatHub.get_instance()
    assert notifier() is hub                      # the hub is the Notifier even before the bot exists
    assert hub.transports == []
    start.assert_not_called()
    assert interface.transport.state == IntegrationState.NOT_CONFIGURED


@pytest.mark.unit
def test_restart_integration_brings_the_bot_up_once_the_token_is_stored():
    """config_request stores the token, restart_integration("Telegram") rebuilds the transport:
    the new instance reads the token, joins the hub and starts; no server restart."""
    interface = TelegramInterface()
    monitor = IntegrationMonitor.get_instance()
    config = _config(token="")
    monitor._config_manager = config
    with patch(f"{TRANSPORT}.config", config), patch(f"{TRANSPORT}.TelegramTransport.start") as start, \
            patch(f"{TRANSPORT}.TelegramTransport.check_health", return_value=True):
        interface.start(MagicMock(), app=None)
        first = interface.transport
        assert first.state == IntegrationState.NOT_CONFIGURED

        stored = _config(token="123:abc")
        monitor._config_manager = stored
        with patch(f"{TRANSPORT}.config", stored):
            result = monitor.restart("Telegram")

    assert result == {"restartable": True, "status": result["status"]}
    assert result["status"]["state"] == "ok"
    second = interface.transport
    assert second is not first and second.is_configured
    hub = ChatHub.get_instance()
    assert hub.transports == [second]
    start.assert_called_once_with(hub)


@pytest.mark.unit
def test_a_running_bot_leaves_the_hub_and_stops_when_the_monitor_rebuilds_it():
    interface = TelegramInterface()
    monitor = IntegrationMonitor.get_instance()
    config = _config()
    monitor._config_manager = config
    with patch(f"{TRANSPORT}.config", config), patch(f"{TRANSPORT}.TelegramTransport.start"), \
            patch(f"{TRANSPORT}.TelegramTransport.stop") as stop, \
            patch(f"{TRANSPORT}.TelegramTransport.check_health", return_value=True):
        interface.start(MagicMock(), app=None)
        old = interface.transport
        hub = ChatHub.get_instance()
        assert hub.transports == [old]
        monitor.restart("Telegram")

    stop.assert_called_once()
    assert interface.transport is not old
    assert hub.transports == [interface.transport]
