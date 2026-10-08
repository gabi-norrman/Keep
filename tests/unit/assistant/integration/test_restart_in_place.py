"""`IntegrationMonitor.restart_in_place`: a sign-in that landed on the running instance starts
it as it is — the start hook, the feed-open wait, the health check — with no rebuild."""
from unittest.mock import Mock, patch

from assistant.integration.Integration import Integration, IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor


class Lamp(Integration):
    """A lamp. It lights. Nothing more."""
    name = "Lamp"
    config_keys = []
    healthy = True
    linked = False

    def check_health(self) -> bool:
        return self.healthy

    def needs_2fa(self) -> bool:
        return not self.linked


def _monitor():
    monitor = IntegrationMonitor()
    monitor._config_manager = Mock()
    monitor._config_manager.integration_enabled.return_value = True
    return monitor


def test_a_still_unlinked_instance_is_not_started():
    Lamp.reset_instance()
    monitor = _monitor()
    start = Mock()
    lamp = monitor.register_class(Lamp, start=start)
    start.reset_mock()
    result = monitor.restart_in_place("Lamp")
    assert result["status"]["state"] == "needs_2fa" and result["restartable"]
    start.assert_not_called()
    assert monitor.find("Lamp") is lamp


def test_a_linked_instance_is_started_in_place_and_checked():
    Lamp.reset_instance()
    monitor = _monitor()
    start = Mock()
    lamp = monitor.register_class(Lamp, start=start)
    start.reset_mock()
    lamp.linked = True
    with patch.object(monitor, "notify") as notify:
        result = monitor.restart_in_place("lamp")
    start.assert_called_once_with(lamp)
    assert monitor.find("Lamp") is lamp      # the same instance, not a rebuilt one
    assert result["status"]["state"] == "ok" and lamp.state == IntegrationState.OK
    notify.assert_called_once()


def test_an_unknown_name_is_none():
    assert _monitor().restart_in_place("Kettle") is None
