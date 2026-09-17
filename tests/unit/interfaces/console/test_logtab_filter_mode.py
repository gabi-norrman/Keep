import pytest
from interfaces.console.tabs.LogTab import LogTab, FilterMode


@pytest.fixture
def tab():
    t = LogTab()
    t.display_height = 20
    return t


class TestFilterModeCycle:
    def test_f_cycles_through_all_modes(self, tab):
        assert tab.filter_mode == FilterMode.NORMAL
        tab.handle_input('f')
        assert tab.filter_mode == FilterMode.INFO
        tab.handle_input('f')
        assert tab.filter_mode == FilterMode.VERBOSE
        tab.handle_input('f')
        assert tab.filter_mode == FilterMode.NORMAL

    def test_mode_change_resets_read_state(self, tab):
        tab.log_lines = ["I│old"]
        tab.last_file_size = 123
        tab.scroll_offset = 5
        tab.handle_input('f')
        assert tab.log_lines == []
        assert tab.last_file_size == 0
        assert tab.scroll_offset == 0


class TestLevelFilter:
    LINES = [
        "I│2026-08-20 │ main │ server started",
        "T│2026-08-20 │ main │   details",
        "E│2026-08-20 │ main │ boom",
        "N│2026-08-20 │ main │ note",
        "F│2026-08-20 │ main │   fine detail",
    ]

    def test_info_mode_drops_trace_and_fine(self, tab):
        tab.filter_mode = FilterMode.INFO
        assert tab._apply_level_filter(self.LINES) == [
            "I│2026-08-20 │ main │ server started",
            "E│2026-08-20 │ main │ boom",
            "N│2026-08-20 │ main │ note",
        ]

    def test_info_mode_strips_ansi_before_checking_level(self, tab):
        tab.filter_mode = FilterMode.INFO
        lines = [
            "\x1b[2m\x1b[90mT\x1b[0m│trace line",
            "\x1b[2m\x1b[90mI\x1b[0m│info line",
        ]
        assert tab._apply_level_filter(lines) == ["\x1b[2m\x1b[90mI\x1b[0m│info line"]

    def test_normal_and_verbose_modes_keep_everything(self, tab):
        for mode in (FilterMode.NORMAL, FilterMode.VERBOSE):
            tab.filter_mode = mode
            assert tab._apply_level_filter(self.LINES) == self.LINES
