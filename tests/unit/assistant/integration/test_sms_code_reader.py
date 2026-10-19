"""The SmsCodeReader contract: `live()` names the registered readers that read the Mac's text
messages now, through the monitor, skipping a stopped one and one that cannot answer."""
from unittest.mock import patch

import pytest

from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from assistant.integration.SmsCodeReader import SmsCodeReader, live


class FakeReader(Integration, SmsCodeReader):
    def __init__(self, reader_name="Fake Messages", reading=True, raises=False):
        super().__init__()
        self._reader_name, self._reading, self._raises = reader_name, reading, raises

    @property
    def name(self): return self._reader_name
    @property
    def config_keys(self): return []
    def check_health(self): return True

    def reads_sms_codes(self):
        if self._raises:
            raise RuntimeError("no runner yet")
        return self._reading


class Weather(Integration):
    @property
    def name(self): return "Weather"
    @property
    def config_keys(self): return []
    def check_health(self): return True


@pytest.fixture
def monitor():
    IntegrationMonitor.reset_instance()
    with patch.object(IntegrationMonitor, "_config_manager", create=True):
        yield IntegrationMonitor.get_instance()
    IntegrationMonitor.reset_instance()


def _register(monitor, *integrations):
    with monitor._lock:
        for integration in integrations:
            monitor._integrations[integration.name] = integration


@pytest.mark.unit
class TestLive:
    def test_the_reading_readers_by_name_in_registration_order(self, monitor):
        _register(monitor, Weather(), FakeReader("Fake Messages"), FakeReader("Other Messages"))
        assert live() == ["Fake Messages", "Other Messages"]

    def test_a_stopped_reader_and_one_that_cannot_answer_are_left_out(self, monitor):
        _register(monitor, FakeReader("Dead", reading=False), FakeReader("Broken", raises=True), FakeReader("Alive"))
        assert live() == ["Alive"]

    def test_nothing_registered_means_nobody_reads(self, monitor):
        _register(monitor, Weather())
        assert live() == []
