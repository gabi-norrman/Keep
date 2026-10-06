import pytest

from assistant.integration.Integration import IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor, UnavailableIntegration
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.IntegrationsGroup import IntegrationsGroup
from interfaces.console.tabs.TabNavigator import TabNavigator

from .integration_fakes import (FakeTab, KettleIntegration, KettleTab, LampIntegration, LampTab, RadioIntegration,
                                RadioTab, disable, enable)
from .test_tab_navigator import FakeTab as PlainTab


@pytest.fixture
def monitor(monkeypatch):
    monitor = IntegrationMonitor()
    monkeypatch.setattr(IntegrationMonitor, "_instance", monitor)
    monkeypatch.setattr(FakeTab, "built", [])
    return monitor


def make_group():
    return IntegrationsGroup([LampTab, KettleTab, RadioTab])


def names(group):
    group.refresh()
    return [tab.name for tab in group.tabs]


def test_a_console_built_before_the_integrations_register_has_the_disabled_tab_alone(monitor):
    group = make_group()

    assert names(group) == ["Disabled"]
    assert group.content_tab().name == "Disabled"
    assert FakeTab.built == []


def test_the_row_follows_the_monitor_in_alphabetical_order_with_disabled_last(monitor):
    group = make_group()
    enable(monitor, RadioIntegration)
    enable(monitor, LampIntegration)
    disable(monitor, KettleIntegration)
    assert names(group) == ["Lamp", "Radio", "Disabled"]

    enable(monitor, KettleIntegration)
    assert names(group) == ["Kettle", "Lamp", "Radio", "Disabled"]

    disable(monitor, LampIntegration)
    assert names(group) == ["Kettle", "Radio", "Disabled"]


def test_the_tab_of_a_disabled_integration_is_never_built(monitor):
    group = make_group()
    enable(monitor, LampIntegration)
    disable(monitor, KettleIntegration)

    group.draw(TUIScreen(100, 30), 0)
    group.draw(TUIScreen(100, 30), 1)

    assert [type(tab) for tab in FakeTab.built] == [LampTab]


@pytest.mark.parametrize("state", [IntegrationState.NOT_CONFIGURED, IntegrationState.NEEDS_2FA, IntegrationState.ERROR])
def test_an_enabled_integration_keeps_its_tab_whatever_its_health(monitor, state):
    group = make_group()
    lamp = LampIntegration()
    monitor.register(lamp)
    lamp.state = state

    assert names(group) == ["Lamp", "Disabled"]


def test_an_integration_whose_constructor_failed_still_has_its_tab(monitor):
    group = make_group()
    monitor.register(UnavailableIntegration(KettleIntegration, RuntimeError("no kettle section")))

    assert names(group) == ["Kettle", "Disabled"]


def test_the_first_tab_is_selected_until_the_user_picks_one(monitor):
    group = make_group()
    assert names(group) == ["Disabled"]
    enable(monitor, LampIntegration)

    group.refresh()

    assert group.content_tab().name == "Lamp"


def test_the_selected_tab_stays_selected_when_the_row_changes_around_it(monitor):
    group = make_group()
    enable(monitor, LampIntegration)
    enable(monitor, RadioIntegration)
    group.on_activate()
    group.select(1)
    radio = group.content_tab()
    assert radio.name == "Radio"
    seen = list(radio.events)

    enable(monitor, KettleIntegration)
    disable(monitor, LampIntegration)
    group.refresh()

    assert group.content_tab() is radio
    assert radio.events == seen


def test_the_tab_on_screen_gives_way_to_its_neighbour_when_its_integration_is_disabled(monitor):
    group = make_group()
    enable(monitor, LampIntegration)
    enable(monitor, RadioIntegration)
    group.on_activate()
    lamp = group.content_tab()

    disable(monitor, LampIntegration)
    group.draw(TUIScreen(100, 30), 0)

    radio = group.content_tab()
    assert radio.name == "Radio"
    assert lamp.events[-1] == "deactivate"
    assert radio.events[:2] == ["activate", "draw"]


def test_a_tab_dropped_off_screen_is_not_told_twice_that_it_left(monitor):
    group = make_group()
    enable(monitor, LampIntegration)
    group.on_activate()
    lamp = group.content_tab()
    group.on_deactivate()

    disable(monitor, LampIntegration)
    group.refresh()

    assert lamp.events.count("deactivate") == 1


def test_an_integration_enabled_again_gets_a_fresh_tab(monitor):
    group = make_group()
    enable(monitor, LampIntegration)
    group.refresh()
    first = group.content_tab()

    disable(monitor, LampIntegration)
    group.refresh()
    enable(monitor, LampIntegration)
    group.refresh()

    assert group.tabs[0].name == "Lamp" and group.tabs[0] is not first


def test_the_tab_walk_enters_the_row_as_it_is_now_not_as_last_drawn(monitor):
    home, group = PlainTab("Home"), make_group()
    nav = TabNavigator([home, group])
    enable(monitor, LampIntegration)
    enable(monitor, KettleIntegration)

    nav.handle_key('shift_tab')
    assert nav.path == "Integrations/Disabled"
    nav.handle_key('shift_tab')
    assert nav.path == "Integrations/Lamp"

    nav.handle_key('tab')
    nav.handle_key('tab')
    nav.handle_key('tab')
    assert nav.path == "Integrations/Kettle"
