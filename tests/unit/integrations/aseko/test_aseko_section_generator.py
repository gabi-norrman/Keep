"""The pool section renders the units of the integration the monitor built; it never builds
one, and while the integration is being rebuilt it is empty rather than failing."""
from unittest.mock import Mock, patch

import pytest

from integrations.aseko.AsekoIntegration import AsekoIntegration
from integrations.aseko.AsekoSectionGenerator import AsekoSectionGenerator

UNIT = {"name": "Garden pool", "serialNumber": "AQ-0001", "online": True,
        "statusValues": {"waterTemperature": 26, "ph": 7.3}, "statusMessages": []}


@pytest.mark.unit
class TestFetchContent:
    def test_without_the_integration_the_section_is_empty_and_nothing_is_built(self):
        AsekoIntegration.reset_instance()
        section = AsekoSectionGenerator(None)
        assert section._fetch_content() == ""
        assert AsekoIntegration.peek_instance() is None

    def test_with_the_integration_the_section_is_the_rendered_units(self):
        api = Mock(spec=AsekoIntegration)
        api.fetch_units.return_value = [UNIT]
        section = AsekoSectionGenerator(None)
        with patch.object(AsekoIntegration, "peek_instance", return_value=api):
            text = section._fetch_content()
        assert "Garden pool: online" in text and "Water: 26°C" in text

    def test_the_section_is_gated_by_its_folder_and_brings_its_own_heading(self):
        monitor = Mock()
        monitor.folder_enabled.return_value = False
        assert AsekoSectionGenerator.folder() == "aseko"
        assert AsekoSectionGenerator.enabled_by(monitor) is False
        monitor.folder_enabled.return_value = True
        assert AsekoSectionGenerator.enabled_by(monitor) is True
        assert AsekoSectionGenerator.heading.startswith("#") and AsekoSectionGenerator.intro

    def test_a_failing_fetch_propagates_so_the_base_keeps_the_last_good_content(self):
        api = Mock(spec=AsekoIntegration)
        api.fetch_units.side_effect = RuntimeError("429")
        section = AsekoSectionGenerator(None)
        with patch.object(AsekoIntegration, "peek_instance", return_value=api), pytest.raises(RuntimeError):
            section._fetch_content()
