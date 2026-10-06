import pytest

from assistant.integration.IntegrationMonitor import IntegrationMonitor
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs import DisabledTab as module
from interfaces.console.tabs.DisabledTab import DisabledTab

from .integration_fakes import KettleIntegration, LampIntegration, RadioIntegration, disable, enable


@pytest.fixture
def monitor(monkeypatch):
    monitor = IntegrationMonitor()
    monkeypatch.setattr(IntegrationMonitor, "_instance", monitor)
    return monitor


def rows(tab, width=140, height=24):
    screen = TUIScreen(width, height)
    tab.draw(screen, 0)
    return ["".join(c.char for c in line).rstrip() for line in screen.next_frame]


def row_of(lines, name):
    return next(line for line in lines if f"● {name}" in line)


def test_it_lists_the_disabled_integrations_and_only_those(monitor):
    enable(monitor, LampIntegration)
    disable(monitor, RadioIntegration)
    disable(monitor, KettleIntegration)

    lines = rows(DisabledTab())

    listed = [line.split("●")[1].split()[0] for line in lines if "●" in line]
    assert listed == ["Kettle", "Radio"]


def test_a_row_says_what_the_integration_is_and_what_enabling_it_brings(monitor):
    disable(monitor, KettleIntegration)
    disable(monitor, RadioIntegration)
    disable(monitor, LampIntegration)

    lines = rows(DisabledTab(frozenset({LampIntegration})))

    kettle, radio, lamp = row_of(lines, "Kettle"), row_of(lines, "Radio"), row_of(lines, "Lamp")
    assert "Boils water on request." in kettle and "second paragraph" not in kettle
    assert "private data" in kettle and "feed" not in kettle and "adds a tab" not in kettle
    assert "feed" in radio and "private data" not in radio
    assert "adds a tab" in lamp and "Switches the reading lamp." in lamp and "how long" not in lamp


def test_the_list_follows_an_integration_being_enabled(monitor):
    disable(monitor, KettleIntegration)
    disable(monitor, LampIntegration)
    tab = DisabledTab()
    assert any("● Lamp" in line for line in rows(tab))

    enable(monitor, LampIntegration)

    assert not any("● Lamp" in line for line in rows(tab))


def test_nothing_registered_reads_as_still_starting_not_as_all_enabled(monitor):
    text = "\n".join(rows(DisabledTab()))

    assert module.STARTING in text and module.ALL_ENABLED not in text


def test_nothing_disabled_says_so(monitor):
    enable(monitor, LampIntegration)

    text = "\n".join(rows(DisabledTab()))

    assert module.ALL_ENABLED in text and module.STARTING not in text


def test_the_arrows_scroll_a_list_longer_than_the_screen(monitor):
    disable(monitor, KettleIntegration)
    disable(monitor, LampIntegration)
    disable(monitor, RadioIntegration)
    tab = DisabledTab()
    height = next(h for h in range(30, 8, -1) if not any("● Radio" in line for line in rows(tab, height=h)))

    assert tab.handle_input('down')

    lines = rows(tab, height=height)
    assert any("● Radio" in line for line in lines) and not any("● Kettle" in line for line in lines)
