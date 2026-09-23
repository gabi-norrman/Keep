"""HomeSectionGenerator: the HOME AUTOMATION DATA section rendered from the active backend's
snapshot, the setup hint without one, and the live Night flag the autonomous guards read."""
from unittest.mock import Mock, patch

import pytest

from assistant.integration.integrations.home.HomeAutomation import Entity, HomeSnapshot, NotConfiguredHome, STARTUP_POLL_INTERVAL
from assistant.prompt.sections.HomeSectionGenerator import HomeSectionGenerator, DEFAULT_NIGHT_VARIABLE

MODULE = "assistant.prompt.sections.HomeSectionGenerator"


def _backend(snapshot, section_filters=None, config_section="fake", name="Fake"):
    backend = Mock(name=name, state_poll_interval=30, section_filters=section_filters or {},
                   config_section=config_section)
    backend.name = name
    backend.snapshot.return_value = snapshot
    return backend


@pytest.fixture
def section():
    return HomeSectionGenerator(prompt_manager=None)


@pytest.mark.unit
class TestFetchContent:
    def test_renders_the_snapshot_with_the_configured_and_backend_filters(self, section):
        snap = HomeSnapshot(lights=[Entity("Lamp (Kök)", "on"), Entity("Noisy lamp", "off")],
                            sensors=[Entity("Humidity", "50"), Entity("Temp", "21.55555")])
        backend = _backend(snap, {"sensors": ("Humidity",)})
        with patch(f"{MODULE}.active", return_value=backend), \
             patch(f"{MODULE}.config") as cfg:
            cfg.get.side_effect = lambda key, default=None: ["noisy"] if key == "fake.prompt_filters" else default
            text = section._fetch_content()
        assert text == '## LIGHTS\n"Lamp [Kök]": on\n\n## SENSORS\n"Temp": 21.56\n\n'
        assert section._latest is snap

    def test_without_a_backend_the_section_is_the_hint(self, section):
        with patch(f"{MODULE}.active", return_value=NotConfiguredHome("Enable Indigo first.")):
            assert section._fetch_content() == "(Enable Indigo first.)"
        assert section._latest is None

    def test_the_poll_interval_follows_the_backend(self, section):
        assert section._poll_interval == 30
        with patch(f"{MODULE}.active", return_value=NotConfiguredHome("Starting.", starting=True)):
            assert section._fetch_content() == "(Starting.)"
        assert section._poll_interval == STARTUP_POLL_INTERVAL
        with patch(f"{MODULE}.active", return_value=_backend(HomeSnapshot())):
            section._fetch_content()
        assert section._poll_interval == 30

    def test_a_missing_prompt_filters_key_means_no_filters(self, section):
        backend = _backend(HomeSnapshot(lights=[Entity("Lamp", "on")]))
        with patch(f"{MODULE}.active", return_value=backend), patch(f"{MODULE}.config") as cfg:
            cfg.get.side_effect = lambda key, default=None: None
            assert section._fetch_content() == '## LIGHTS\n"Lamp": on\n\n'

    def test_a_raising_backend_propagates_so_the_last_good_content_is_kept(self, section):
        backend = _backend(None)
        backend.snapshot.side_effect = RuntimeError("ssh down")
        with patch(f"{MODULE}.active", return_value=backend), pytest.raises(RuntimeError):
            section._fetch_content()

@pytest.mark.unit
class TestIsNight:
    @pytest.fixture(autouse=True)
    def night_variable(self):
        self.configured = {}
        with patch(f"{MODULE}.config") as cfg:
            cfg.get.side_effect = lambda key, default=None: self.configured.get(key, default)
            yield

    def test_reads_the_live_snapshot(self, section):
        section._latest = HomeSnapshot(variables=[Entity(DEFAULT_NIGHT_VARIABLE, "true")])
        assert section.is_night() is True
        section._latest = HomeSnapshot(variables=[Entity(DEFAULT_NIGHT_VARIABLE, "false")])
        assert section.is_night() is False

    def test_reads_the_configured_variable(self, section):
        self.configured["home.night_variable"] = "Sleeping"
        section._latest = HomeSnapshot(variables=[Entity(DEFAULT_NIGHT_VARIABLE, "true")])
        assert section.is_night() is False
        section._latest = HomeSnapshot(variables=[Entity("Sleeping", "true")])
        assert section.is_night() is True

    def test_no_snapshot_or_no_variable_is_not_night(self, section):
        assert section.is_night() is False
        section._latest = HomeSnapshot()
        assert section.is_night() is False

@pytest.mark.unit
class TestPollCadence:
    def test_polls_fast_while_starting_then_at_the_backends_interval(self, section):
        """The loop sleeps for the interval the last fetch set — 2 s until a backend lands,
        its own after — and resolves the backend once per cycle."""
        waits = []

        def wait(seconds):
            waits.append(seconds)
            if len(waits) == 2:
                section._stop_event.set()
            return False

        backends = [NotConfiguredHome("Starting.", starting=True), _backend(HomeSnapshot(lights=[Entity("Lamp", "on")]))]
        with patch(f"{MODULE}.active", side_effect=backends) as active, \
             patch.object(section, "_wait", wait), \
             patch(f"{MODULE}.config") as cfg:
            cfg.get.side_effect = lambda key, default=None: default
            section._start_monitoring()
        assert waits == [STARTUP_POLL_INTERVAL, 30]
        assert active.call_count == 2
        assert section.content == '## LIGHTS\n"Lamp": on\n\n'
