"""The HomeKit section reads the integration the monitor built, never builds one, and says
plainly when the app is not running instead of keeping stale readings."""
from unittest.mock import Mock, patch

import pytest

from integrations.homeclaw.HomeClawClient import HomeClawError, HomeClawUnavailable
from integrations.homeclaw.HomeClawIntegration import HomeClawIntegration
from integrations.homeclaw.HomeClawSectionGenerator import HomeClawSectionGenerator


def _section():
    with patch("integrations.homeclaw.HomeClawSectionGenerator.config") as cfg:
        cfg.get.side_effect = lambda key, default=None: default
        return HomeClawSectionGenerator(None)


def test_without_the_integration_the_section_is_empty_and_nothing_is_built():
    HomeClawIntegration.reset_instance()
    assert _section()._fetch_content() == ""
    assert HomeClawIntegration.peek_instance() is None


def test_the_section_is_the_snapshot():
    homeclaw = Mock(spec=HomeClawIntegration)
    homeclaw.snapshot.return_value = "## Patio\n- Awning Roof (window covering): open [B2000001]"
    with patch.object(HomeClawIntegration, "peek_instance", return_value=homeclaw):
        assert "Awning Roof" in _section()._fetch_content()


def test_the_app_not_running_is_said_instead_of_stale_data():
    homeclaw = Mock(spec=HomeClawIntegration)
    homeclaw.snapshot.side_effect = HomeClawUnavailable("HomeClaw is not running (no socket)")
    with patch.object(HomeClawIntegration, "peek_instance", return_value=homeclaw):
        assert "not running" in _section()._fetch_content()


def test_another_failure_propagates_so_the_base_keeps_the_last_good_content():
    homeclaw = Mock(spec=HomeClawIntegration)
    homeclaw.snapshot.side_effect = HomeClawError("HomeClaw did not answer list_accessories within 15 s")
    with patch.object(HomeClawIntegration, "peek_instance", return_value=homeclaw), pytest.raises(HomeClawError):
        _section()._fetch_content()


def test_the_refresh_cadence_is_the_config_key_with_a_floor():
    with patch("integrations.homeclaw.HomeClawSectionGenerator.config") as cfg:
        cfg.get.side_effect = lambda key, default=None: {"homeclaw.refresh_seconds": 5}.get(key, default)
        assert HomeClawSectionGenerator(None).UPDATE_INTERVAL == 15


def test_the_section_is_gated_by_its_folder():
    monitor = Mock()
    monitor.folder_enabled.return_value = False
    assert HomeClawSectionGenerator.folder() == "homeclaw"
    assert HomeClawSectionGenerator.enabled_by(monitor) is False
