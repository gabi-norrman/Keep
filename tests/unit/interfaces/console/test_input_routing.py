from unittest.mock import patch

import pytest

from interfaces.console.TUIFloxyConsole import TUIFloxyConsole
from interfaces.console.StartupIntro import StartupIntro
from interfaces.console.crystal.Scene import Scene
from interfaces.console.tabs.TabGroup import TabGroup
from interfaces.console.tabs.TabNavigator import TabNavigator
from tests.unit.interfaces.console.test_tab_navigator import FakeTab

ADDR = ("127.0.0.1", 1)
SHIFT_RIGHT = '\x1b[1;2C'
SHIFT_UP = '\x1b[1;2A'
SHIFT_TAB = '\x1b[Z'
SHIFT_ENTER = '\x1b[13;2u'
CTRL_X = '\x18'


class CapturingTab(FakeTab):
    """A tab with an open text field: takes every key except the ones it says no to."""

    def __init__(self, name, refuse=()):
        super().__init__(name)
        self.refuse = refuse

    @property
    def captures_text(self):
        return True

    def handle_input(self, key):
        self.events.append(key)
        return key not in self.refuse


def make_console(tabs):
    console = TUIFloxyConsole.__new__(TUIFloxyConsole)
    console.navigator = TabNavigator(tabs)
    console._force_clear_screen = False
    console.intro = StartupIntro(Scene())
    console.speech_analyzer = None
    console.outpost_server = None
    console.reset()
    return console


@pytest.mark.unit
class TestInputRouting:
    def test_capturing_tab_gets_the_tab_switching_keys(self):
        editor, other = CapturingTab("Home"), FakeTab("Log")
        console = make_console([editor, other])
        console.input_callback('/', ADDR)
        console.input_callback('|', ADDR)
        assert editor.events == ['/', '|']
        assert console.navigator.active_tab is editor
        assert console._force_clear_screen is False

    def test_non_capturing_tab_loses_slash_to_navigation(self):
        home, log = FakeTab("Home"), FakeTab("Log")
        console = make_console([home, log])
        console.input_callback('/', ADDR)
        assert console.navigator.active_tab is log
        assert '/' not in home.events
        assert console._force_clear_screen is True

    def test_shift_arrows_bypass_a_capturing_tab(self):
        editor, other = CapturingTab("Home"), FakeTab("Log")
        console = make_console([editor, other])
        console.input_callback(SHIFT_RIGHT, ADDR)
        assert console.navigator.active_tab is other
        assert 'shift_right' not in editor.events

    def test_shift_enter_is_text_and_reaches_a_capturing_tab(self):
        editor, other = CapturingTab("Home"), FakeTab("Log")
        console = make_console([editor, other])
        console.input_callback(SHIFT_ENTER, ADDR)
        assert editor.events == ['shift_enter']
        assert console.navigator.active_tab is editor

    def test_capture_is_decided_by_the_group_content_tab(self):
        editor, plain = CapturingTab("Search"), FakeTab("Plain")
        group = TabGroup("Developer", [editor, plain])
        console = make_console([FakeTab("Home"), group])
        console.navigator.handle_key('shift_right')
        console.input_callback('/', ADDR)
        assert console.navigator.active_tab is group
        assert editor.events[-1] == '/'

    def test_global_shortcut_still_fires_when_the_capturing_tab_declines_it(self):
        editor = CapturingTab("Home", refuse=(CTRL_X,))
        console = make_console([editor, FakeTab("Log")])
        with patch.object(TUIFloxyConsole, "_broadcast_outpost_command") as broadcast:
            console.input_callback(CTRL_X, ADDR)
        broadcast.assert_called_once_with("listen")
        assert editor.events == [CTRL_X]

    def test_capturing_tab_that_consumes_ctrl_x_keeps_it_from_the_outposts(self):
        editor = CapturingTab("Home")
        console = make_console([editor, FakeTab("Log")])
        with patch.object(TUIFloxyConsole, "_broadcast_outpost_command") as broadcast:
            console.input_callback(CTRL_X, ADDR)
        broadcast.assert_not_called()

    def test_tab_bypasses_a_capturing_tab(self):
        # A Log search field takes every key, but Tab and Shift+Tab are navigation
        log, prompt = CapturingTab("Log"), FakeTab("Prompt")
        group = TabGroup("Developer", [log, prompt])
        console = make_console([group, FakeTab("Home")])
        console.input_callback('\t', ADDR)
        assert group.content_tab() is prompt and console.navigator.sub_focused
        assert log.events == ["deactivate"], "Tab never reached the search field"
        assert console._force_clear_screen is True
        console.input_callback(SHIFT_TAB, ADDR)
        assert group.content_tab() is log
        assert 'shift_tab' not in log.events

    def test_tab_reaches_a_tab_revealing_a_masked_field(self):
        class MaskedFieldTab(CapturingTab):
            claims_tab = True

        vault = MaskedFieldTab("Vault")
        console = make_console([vault, FakeTab("Log")])
        console.input_callback('\t', ADDR)
        console.input_callback(SHIFT_TAB, ADDR)
        assert vault.events == ['tab', 'shift_tab']
        assert console.navigator.active_tab is vault
        assert console._force_clear_screen is False

    def test_shift_up_outside_a_group_pages_the_tab(self):
        home = FakeTab("Home")
        console = make_console([home, FakeTab("Log")])
        console.input_callback(SHIFT_UP, ADDR)
        assert home.events == ['pageup']

    def test_unknown_escape_sequence_is_dropped(self):
        home = FakeTab("Home")
        console = make_console([home])
        console.input_callback('\x1b[200~', ADDR)
        assert home.events == []


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.mark.unit
class TestStartupIntroOwnership:
    """While the intro plays, a key skips it instead of reaching any tab or shortcut, and the
    frame is the intro's alone: no tab bar, no tab draw, until it hands over."""

    def _console_mid_flight(self):
        from interfaces.console.framework.TUIScreen import TUIScreen
        home, log = CapturingTab("Home"), FakeTab("Log")
        console = make_console([home, log])
        clock = Clock()
        scene = Scene(clock)
        scene.aa = 1
        console.intro = StartupIntro(scene)
        with patch.object(console, "_draw_tab_bar") as bar:
            console.draw_screen(0, TUIScreen(80, 24))
        assert console.intro.playing and bar.call_count == 0
        return console, home, log, scene, clock

    def test_a_key_skips_the_flight_and_reaches_no_tab(self):
        console, home, log, scene, _ = self._console_mid_flight()
        console.input_callback('/', ADDR)
        console.input_callback(CTRL_X, ADDR)
        assert home.events == [] and log.events == []
        assert console.navigator.active_tab is home, "no navigation either"
        assert scene.flight == scene.intro.length(), "the flight jumped to its end"

    def test_frames_belong_to_the_intro_until_it_hands_over(self):
        from interfaces.console.framework.TUIScreen import TUIScreen
        from interfaces.console.StartupIntro import Title
        console, home, log, scene, clock = self._console_mid_flight()
        console.intro.skip()
        with patch.object(console, "_draw_tab_bar") as bar:
            console.draw_screen(1, TUIScreen(80, 24))       # lands, the title starts typing
            clock.now += len(Title.TEXT) / Title.CPS + 1
            console.draw_screen(2, TUIScreen(80, 24))       # typed, holding
            assert bar.call_count == 0 and "draw" not in home.events and console.intro.playing
            clock.now += StartupIntro.HOLD_SECONDS
            console.draw_screen(3, TUIScreen(80, 24))       # over to the tabs
        assert not console.intro.playing
        assert bar.call_count == 1 and home.events == ["draw"]
