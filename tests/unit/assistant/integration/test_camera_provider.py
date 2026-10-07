"""The CameraProvider contract: the camera pick (`select`), `active()` gathering the ready
providers through the monitor, and the hint for when none is."""
from unittest.mock import patch

import pytest

from assistant.integration.CameraProvider import CameraProvider, STARTING_HINT, active, no_provider_hint, select
from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import IntegrationMonitor, DisabledIntegration, UnavailableIntegration


class FakeCameras(Integration, CameraProvider):
    def __init__(self, missing=()):
        super().__init__()
        self._missing = list(missing)

    @property
    def name(self): return "Fake Cameras"
    @property
    def config_keys(self): return ["fake.username", "fake.password"]
    def check_health(self): return True
    def missing_config_keys(self): return self._missing
    def camera_names(self): return ["Front", "Back"]
    def fetch_snapshots(self, only=None): return []


class OtherCameras(FakeCameras):
    @property
    def name(self): return "Other Cameras"


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


def _register(monitor, integration):
    with monitor._lock:
        monitor._integrations[integration.name] = integration


@pytest.mark.unit
class TestSelect:
    NAMES = ["Front Door", "Backyard", "Garage Cam", "Pool"]

    def test_everything_without_filters(self):
        assert select(self.NAMES) == self.NAMES

    def test_an_ignore_keyword_drops_by_substring_whatever_the_case(self):
        assert select(self.NAMES, ["garage", "POOL"]) == ["Front Door", "Backyard"]

    def test_asking_for_cameras_narrows_by_substring(self):
        assert select(self.NAMES, only=["front", "yard"]) == ["Front Door", "Backyard"]

    def test_an_ignored_camera_stays_out_even_when_asked_for(self):
        assert select(self.NAMES, ["pool"], only=["pool"]) == []

    def test_blank_entries_are_not_filters(self):
        assert select(self.NAMES, [""], only=["", "  "]) == self.NAMES


@pytest.mark.unit
class TestActive:
    def test_the_configured_providers_in_registration_order(self, monitor):
        first, second = FakeCameras(), OtherCameras()
        _register(monitor, first)
        _register(monitor, Weather())
        _register(monitor, second)
        assert active() == [first, second]

    def test_an_unconfigured_provider_is_left_out(self, monitor):
        ready = FakeCameras()
        _register(monitor, OtherCameras(missing=["fake.password"]))
        _register(monitor, ready)
        assert active() == [ready]

    def test_nothing_registered_is_an_empty_list(self, monitor):
        assert active() == []


@pytest.mark.unit
class TestNoProviderHint:
    def test_nothing_registered_says_still_starting(self, monitor):
        _register(monitor, DisabledIntegration(Weather))
        assert no_provider_hint() == STARTING_HINT

    def test_an_unconfigured_provider_names_its_missing_keys(self, monitor):
        _register(monitor, FakeCameras(missing=["fake.password"]))
        hint = no_provider_hint()
        assert "Fake Cameras" in hint and "fake.password" in hint and "config_help" in hint

    def test_a_disabled_provider_gives_the_enable_line(self, monitor):
        _register(monitor, DisabledIntegration(FakeCameras))
        assert 'enable_integration("Fake Cameras")' in no_provider_hint()

    def test_a_broken_provider_carries_its_error(self, monitor):
        _register(monitor, UnavailableIntegration(OtherCameras, KeyError("ring")))
        hint = no_provider_hint()
        # A placeholder for a class that never got built is named after the class.
        assert "OtherCameras could not be started" in hint and "KeyError" in hint and "restart_integration" in hint

    def test_a_broken_non_camera_integration_is_not_a_camera_problem(self, monitor):
        _register(monitor, UnavailableIntegration(Weather, KeyError("weather")))
        assert no_provider_hint() == STARTING_HINT
