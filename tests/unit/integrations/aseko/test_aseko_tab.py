"""AsekoTab's board: the summary tiles from the first unit, a facts tile per unit and the
messages table. Aseko is never called: the units are set by hand, and a refresh runs inline."""
from unittest.mock import Mock, patch

import pytest

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen
from integrations.aseko.AsekoIntegration import AsekoIntegration
from integrations.aseko.AsekoTab import AsekoTab

UNIT = {
    "name": "Garden pool", "serialNumber": "AQ-0001", "online": True,
    "statusValues": {"waterTemperature": 27.4, "waterTemperatureRequired": 28, "ph": 7.21, "phRequired": 7.2,
                     "clFree": 0.62, "clFreeRequired": 0.6, "clFreeRequiredUnit": "mg/l", "mode": "AUTO",
                     "waterLevelState": "LOW", "waterLevel": 3},
    "statusMessages": [{"type": "WATER_LEVEL_TOO_LOW", "severity": "ERROR", "message": "Water level too low",
                        "detail": "Fill the pool"}],
}


def make_tab(units=(UNIT,)):
    tab = AsekoTab()
    tab._units = list(units)
    return tab


def draw(tab, width=120, height=40):
    screen = TUIScreen(width, height)
    tab.draw(screen, 0)
    return screen


def rows(screen):
    return ["".join(c.char for c in line).rstrip() for line in screen.next_frame]


def cell_color(screen, lines, needle):
    line = next(l for l in lines if needle in l)
    return screen.next_frame[lines.index(line)][line.index(needle)].style.fg


@pytest.mark.unit
class TestBoard:
    def test_the_tiles_carry_the_first_units_water_ph_and_chlorine(self):
        lines = rows(draw(make_tab(), width=140))     # room for all four tiles
        values = next(l for l in lines if "27.4°C" in l)
        assert "7.21" in values and "0.62 mg/l" in values and "online" in values
        assert any("target 28°C" in l and "target 7.2" in l for l in lines)

    def test_every_reading_is_a_row_and_an_alarm_is_red(self):
        screen = draw(make_tab())
        lines = rows(screen)
        assert any(l.strip().startswith("Water") and "27.4°C (target 28°C)" in l for l in lines)
        assert cell_color(screen, lines, "LOW (alarm)") == Theme.COLOR_RED

    def test_the_units_messages_are_tabled_with_their_severity(self):
        screen = draw(make_tab())
        lines = rows(screen)
        row = next(l for l in lines if "Water level too low" in l)
        assert "ERROR" in row and "Fill the pool" in row
        assert cell_color(screen, lines, "ERROR") == Theme.COLOR_RED

    def test_a_ph_off_its_target_turns_the_tile_yellow(self):
        off = dict(UNIT, statusValues=dict(UNIT["statusValues"], ph=7.8))
        screen = draw(make_tab([off]))
        assert cell_color(screen, rows(screen), "7.8") == Theme.COLOR_YELLOW

    def test_two_units_stand_side_by_side_and_the_table_names_them(self):
        spa = dict(UNIT, name="Spa", statusMessages=[{"severity": "WARNING", "message": "Too much salt per hour"}])
        lines = rows(draw(make_tab([UNIT, spa]), width=160))
        assert any("Garden pool" in l and "Spa" in l for l in lines)        # the two facts tiles' title row
        assert any("Spa" in l and "Too much salt" in l for l in lines)       # the table's unit column

    def test_no_units_yet_shows_the_notice_not_a_board(self):
        tab = make_tab([])
        tab._message = "Aseko Pool is not running"
        lines = rows(draw(tab))
        assert any("Aseko Pool is not running" in l for l in lines)
        assert not any("Unit messages" in l for l in lines)


@pytest.mark.unit
class TestRefresh:
    def test_ctrl_r_fetches_through_the_running_integration(self):
        api = Mock(spec=AsekoIntegration)
        api.fetch_units.return_value = [UNIT]
        tab = make_tab([])
        tab._run = lambda fn: fn()
        with patch.object(AsekoIntegration, "peek_instance", return_value=api):
            assert tab.handle_input('\x12')
        assert tab._units == [UNIT] and tab._message.startswith("Fetched")

    def test_a_failed_fetch_keeps_the_old_units_and_says_why(self):
        api = Mock(spec=AsekoIntegration)
        api.fetch_units.side_effect = RuntimeError("Aseko rate limit hit (429)")
        tab = make_tab()
        tab._run = lambda fn: fn()
        with patch.object(AsekoIntegration, "peek_instance", return_value=api):
            tab._refresh()
        assert tab._units == [UNIT] and "429" in tab._message and tab._loading is False

    def test_activation_takes_what_the_section_last_fetched_without_a_call(self):
        api = Mock(spec=AsekoIntegration)
        api.last_units = [UNIT]
        tab = make_tab([])
        with patch.object(AsekoIntegration, "peek_instance", return_value=api):
            tab.on_activate()
        assert tab._units == [UNIT]
        api.fetch_units.assert_not_called()

    def test_without_the_integration_a_refresh_only_says_so(self):
        AsekoIntegration.reset_instance()
        tab = make_tab([])
        tab._refresh()
        assert "not running" in tab._message
