import pytest

from assistant.integration.Integration import IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor, UnavailableIntegration
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs import CatalogTab as module
from interfaces.console.tabs.CatalogTab import CatalogTab

from .integration_fakes import (FakeIntegration, KettleIntegration, LampIntegration, RadioIntegration, disable, enable,
                                named)

ENABLED = module.STATUS[IntegrationState.OK].label
DISABLED = module.STATUS[IntegrationState.DISABLED].label


class ToasterIntegration(FakeIntegration):
    """Toasts bread."""
    name = named("Toaster")
    config_keys = ["toaster.token"]


@pytest.fixture
def monitor(monkeypatch):
    monitor = IntegrationMonitor()
    monkeypatch.setattr(IntegrationMonitor, "_instance", monitor)
    return monitor


def working(monitor, cls):
    enable(monitor, cls)
    monitor.find(cls.config_name()).state = IntegrationState.OK


def rows(tab, width=140, height=24):
    screen = TUIScreen(width, height)
    tab.draw(screen, 0)
    return ["".join(c.char for c in line).rstrip() for line in screen.next_frame]


def row_of(lines, name):
    return next(line for line in lines if f"● {name}" in line)


def test_it_lists_every_integration_the_enabled_ones_first(monitor):
    disable(monitor, KettleIntegration)
    working(monitor, RadioIntegration)
    disable(monitor, LampIntegration)

    lines = rows(CatalogTab())

    listed = [line.split("●")[1].split()[0] for line in lines if "●" in line]
    assert listed == ["Radio", "Kettle", "Lamp"]


def test_a_row_says_whether_the_integration_is_enabled(monitor):
    working(monitor, LampIntegration)
    disable(monitor, KettleIntegration)

    lines = rows(CatalogTab())

    lamp, kettle = row_of(lines, "Lamp"), row_of(lines, "Kettle")
    assert ENABLED in lamp and DISABLED not in lamp
    assert DISABLED in kettle and ENABLED not in kettle


def test_a_row_says_what_the_integration_is_and_what_it_brings(monitor):
    disable(monitor, KettleIntegration)
    disable(monitor, RadioIntegration)
    working(monitor, LampIntegration)

    lines = rows(CatalogTab(frozenset({LampIntegration})))

    kettle, radio, lamp = row_of(lines, "Kettle"), row_of(lines, "Radio"), row_of(lines, "Lamp")
    assert "Boils water on request." in kettle and "second paragraph" not in kettle
    assert "private data" in kettle and "feed" not in kettle and "own tab" not in kettle
    assert "feed" in radio and "private data" not in radio
    assert "own tab" in lamp and "Switches the reading lamp." in lamp and "how long" not in lamp


def test_a_row_follows_its_integration_being_enabled(monitor):
    disable(monitor, LampIntegration)
    tab = CatalogTab()
    assert DISABLED in row_of(rows(tab), "Lamp")

    working(monitor, LampIntegration)

    assert DISABLED not in row_of(rows(tab), "Lamp")


def test_nothing_registered_reads_as_still_starting(monitor):
    assert module.STARTING in "\n".join(rows(CatalogTab()))


def test_an_enabled_integration_that_is_not_working_is_named_as_needing_attention(monitor):
    working(monitor, KettleIntegration)
    enable(monitor, LampIntegration)
    monitor.find("Lamp").state = IntegrationState.NEEDS_2FA

    tiles = dict((label, (value, segments)) for label, value, _, segments in CatalogTab._tiles(CatalogTab()._entries()))

    value, segments = tiles["Needs attention"]
    assert value == "1"
    assert ["Lamp" in text for text, _ in segments] == [True]


def test_the_arrows_pick_the_integration_shown_in_full(monitor):
    working(monitor, LampIntegration)
    disable(monitor, KettleIntegration)
    tab = CatalogTab()
    text = "\n".join(rows(tab, width=180, height=40))
    assert "disable Lamp" in text and "enable Kettle" not in text

    assert tab.handle_input('down')

    text = "\n".join(rows(tab, width=180, height=40))
    assert "enable Kettle" in text and "disable Lamp" not in text


def test_the_selected_integration_says_why_it_could_not_be_built(monitor):
    monitor.register(UnavailableIntegration(KettleIntegration, RuntimeError("no kettle section")))

    text = "\n".join(rows(CatalogTab(), width=180, height=40))

    assert "no kettle section" in text


def test_the_selected_integration_names_the_config_keys_it_misses(monitor):
    enable(monitor, ToasterIntegration)

    text = "\n".join(rows(CatalogTab(), width=180, height=40))

    assert monitor.find("Toaster").state is IntegrationState.NOT_CONFIGURED
    assert "toaster.token" in text


def test_the_selection_scrolls_a_list_longer_than_the_screen(monitor):
    disable(monitor, KettleIntegration)
    disable(monitor, LampIntegration)
    disable(monitor, RadioIntegration)
    tab = CatalogTab()
    height = next(h for h in range(30, 8, -1) if not any("● Radio" in line for line in rows(tab, height=h)))

    tab.handle_input('down')
    tab.handle_input('down')

    lines = rows(tab, height=height)
    assert any("● Radio" in line for line in lines) and not any("● Kettle" in line for line in lines)
