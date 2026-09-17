"""DashboardTab._draw_columns: the one chart core every dashboard chart goes through."""
import pytest

from interfaces.console.framework.TUIScreen import Color, TUIScreen
from interfaces.console.tabs.DashboardTab import DashboardTab

C = Color(200, 100, 50)
CHART_X, CHART_Y, CHART_W, CHART_H = 2, 2, 5, 4


def make_tab():
    return DashboardTab.__new__(DashboardTab)   # no monitors: _draw_columns needs none


def column(screen, x):
    return "".join(screen.next_frame[CHART_Y - 1 + row][x - 1].char for row in range(CHART_H))


def draw(history, max_value=100.0, guides=None):
    screen = TUIScreen(20, 10)
    make_tab()._draw_columns(screen, CHART_X, CHART_Y, CHART_W, CHART_H, history, max_value,
                             lambda v: C, guides)
    return screen


@pytest.mark.unit
class TestDrawColumns:
    def test_the_newest_sample_is_the_rightmost_column_and_short_history_pads_left(self):
        screen = draw([0.0, 0.0, 100.0])
        assert column(screen, CHART_X + 4) == "████"
        assert all(column(screen, CHART_X + i) == "    " for i in range(4))

    def test_long_history_keeps_the_tail(self):
        screen = draw([100.0] * 10 + [0.0] * 5)
        assert all(column(screen, CHART_X + i) == "    " for i in range(5))
        screen = draw([0.0] * 10 + [100.0] * 5)
        assert all(column(screen, CHART_X + i) == "████" for i in range(5))

    def test_a_partial_row_uses_a_fractional_block(self):
        screen = draw([0.0, 0.0, 0.0, 0.0, 62.5])     # 2.5 rows of 4
        assert column(screen, CHART_X + 4) == " \u2584██"

    def test_a_zero_scale_draws_an_empty_chart_instead_of_raising(self):
        screen = draw([1.0, 2.0], max_value=0.0)
        assert all(column(screen, CHART_X + i) == "    " for i in range(CHART_W))

    def test_a_full_block_carries_its_colour_as_background(self):
        # Terminal.app leaves seams between cells; the same colour behind the block hides them
        screen = draw([0.0, 0.0, 0.0, 0.0, 62.5])
        full = screen.next_frame[CHART_Y - 1 + 3][CHART_X + 3].style      # bottom row, full block
        assert full.bg == full.fg
        partial = screen.next_frame[CHART_Y - 1 + 1][CHART_X + 3].style   # the half block on top
        assert partial.bg == DashboardTab.COLOR_PANEL_BG, "a partial block keeps the ground so its height reads"

    def test_guides_show_only_where_no_bar_covers_them(self):
        screen = draw([0.0, 0.0, 0.0, 0.0, 75.0], guides={1: C, 3: C})   # rows count up from the base
        assert column(screen, CHART_X) == "┄ ┄ "
        assert column(screen, CHART_X + 4) == "┄███"


# --- Feeds panel -------------------------------------------------------------------------------

from unittest.mock import patch

from interfaces.console.widgets.FeedStrip import SYMBOL_FAILED, SYMBOL_PAUSED, SYMBOL_RUNNING

PANEL_X, PANEL_Y, PANEL_W, PANEL_H = 1, 1, 64, 6


def status(state="live", **extra) -> dict:
    base = {"state": state, "paused": False, "items": "items", "stored": 23436, "remaining": None,
            "total": None, "progress": None, "eta_seconds": None, "last_error": None}
    base.update(extra)
    return base


class StubFeeds:
    def __init__(self, feeds: dict):
        self.feeds = feeds

    def names(self):
        return list(self.feeds)

    def status(self, name):
        return self.feeds.get(name)


def draw_feeds(feeds: dict, frame: int = 0, width: int = PANEL_W):
    tab = make_tab()
    tab._frame = frame
    screen = TUIScreen(width + 4, 8)
    screen.clear_buffer()
    with patch("interfaces.console.tabs.DashboardTab.Feeds.get_instance", return_value=StubFeeds(feeds)):
        tab._draw_feeds_panel(screen, PANEL_X, PANEL_Y, width, PANEL_H)
    return screen


def row_text(screen, row: int) -> str:
    return "".join(c.char for c in screen.next_frame[PANEL_Y - 1 + row]).rstrip()


@pytest.mark.unit
class TestFeedRow:
    def test_live_is_a_full_green_bar_with_the_count_and_the_feeds_noun(self):
        row = DashboardTab._feed_row("iMessage", status(items="messages"))
        assert (row.symbol, row.progress, row.text) == (SYMBOL_RUNNING, 1.0, "23436 messages")
        assert row.fg == DashboardTab.COLOR_GREEN
        assert DashboardTab._feed_row("Indigo", status(stored=512, items="events")).text == "512 events", \
            "an uncountable feed shows its rows"

    def test_a_catch_up_whose_counts_meet_shows_the_count_once(self):
        assert DashboardTab._feed_row("Photos", status("catching_up", stored=10, total=10, remaining=0)).text == "10 items"

    def test_catching_up_with_a_count_shows_done_over_total_without_separators(self):
        row = DashboardTab._feed_row("iCloud Photos", status("catching_up", progress=0.34, stored=1234, total=23356, remaining=22122))
        assert (row.symbol, row.name, row.progress, row.text) == (DashboardTab.SYMBOL_CATCHING_UP, "Photos", 0.34, "1234 / 23356 items")
        assert row.fg == DashboardTab.COLOR_YELLOW

    def test_catching_up_with_an_eta_appends_it_in_one_rounded_unit(self):
        def text(eta):
            return DashboardTab._feed_row("Photos", status("catching_up", stored=1234, total=22313, remaining=21079, eta_seconds=eta)).text
        assert text(13 * 3600) == "1234 / 22313 items ETA: 13h"
        assert text(13 * 60 + 20) == "1234 / 22313 items ETA: 13m"
        assert text(45) == "1234 / 22313 items ETA: 45s"
        assert text(3 * 86400) == "1234 / 22313 items ETA: 3d"
        assert text(47 * 3600) == "1234 / 22313 items ETA: 47h", "days only from two days up"
        assert text(None) == "1234 / 22313 items"

    def test_catching_up_without_a_count_is_indeterminate_and_shows_its_rows(self):
        row = DashboardTab._feed_row("Indigo", status("catching_up", stored=1345))
        assert (row.progress, row.text) == (None, "1345 items")

    def test_paused_hollows_the_dot_fades_the_colour_and_says_only_that(self):
        row = DashboardTab._feed_row("Indigo", status("catching_up", progress=0.5, stored=5, total=10, remaining=5, paused=True))
        assert (row.symbol, row.text, row.progress) == (SYMBOL_PAUSED, "paused", 0.5)
        assert row.fg != DashboardTab.COLOR_YELLOW

    def test_stopped_and_failed_are_red_empty_bars_saying_only_that(self):
        row = DashboardTab._feed_row("Telegram", status("stopped", last_error="no session file"))
        assert (row.symbol, row.progress, row.text) == (SYMBOL_FAILED, 0.0, "stopped")
        assert row.fg == DashboardTab.COLOR_RED
        failed = DashboardTab._feed_row("Telegram", status("failed", paused=True))
        assert (failed.symbol, failed.text) == (SYMBOL_FAILED, "failed"), "a dead feed is never shown as paused"

    def test_starting_is_a_grey_empty_bar(self):
        row = DashboardTab._feed_row("iMessage", status("starting"))
        assert (row.symbol, row.progress, row.text) == (SYMBOL_RUNNING, 0.0, "starting")
        assert row.fg == DashboardTab.COLOR_DIM


@pytest.mark.unit
class TestDrawFeedsPanel:
    def test_one_row_per_feed_with_name_symbol_bar_and_count(self):
        screen = draw_feeds({
            "iMessage": status(),
            "iCloud Photos": status("catching_up", progress=0.34, stored=1234, total=23356, remaining=22122),
            "Telegram": status("stopped", last_error="no session file"),
        })
        assert " Feeds " in row_text(screen, 0)
        assert row_text(screen, 1).startswith(f"│  iMessage {SYMBOL_RUNNING} █")
        assert "▏ 23436 " in row_text(screen, 1)
        assert row_text(screen, 2).startswith(f"│  Photos   {DashboardTab.SYMBOL_CATCHING_UP} █")
        assert "▏ 1234 / 23356" in row_text(screen, 2)
        assert row_text(screen, 3).startswith(f"│  Telegram {SYMBOL_FAILED}  ")
        assert row_text(screen, 3).rstrip("│ ").endswith("▏ stopped")

    def test_the_bar_takes_every_cell_the_longest_text_leaves(self):
        screen = draw_feeds({"a": status(stored=5), "b": status(stored=1234567)})
        assert row_text(screen, 2).endswith("▏ 1234567 items │")
        assert row_text(screen, 1).index("▏") == row_text(screen, 2).index("▏"), "one text column for every row"

    def test_the_count_is_drawn_in_the_row_colour_not_grey(self):
        screen = draw_feeds({"iMessage": status()})
        line = row_text(screen, 1)
        cell = screen.next_frame[PANEL_Y][line.index("23436")]
        assert cell.style.fg == DashboardTab.COLOR_GREEN

    def test_the_bar_fill_follows_the_progress(self):
        screen = draw_feeds({"a": status(), "b": status("catching_up", progress=0.5), "c": status("stopped")})
        full = row_text(screen, 1).count("█")
        half = row_text(screen, 2).count("█")
        assert full > 0 and abs(half - full / 2) <= 1
        assert "█" not in row_text(screen, 3)

    def test_an_indeterminate_feed_sweeps_a_notch_across_the_bar(self):
        feeds = {"Indigo": status("catching_up")}
        positions = {row_text(draw_feeds(feeds, frame=f), 1).index("▌") for f in (0, 15, 30, 45)}
        assert len(positions) == 4, "the notch moves with the frame"
        bounce = DashboardTab.FEED_SWEEP_FRAMES
        assert row_text(draw_feeds(feeds, frame=10), 1) == row_text(draw_feeds(feeds, frame=bounce - 10), 1)

    def test_no_running_feed_says_so(self):
        screen = draw_feeds({})
        assert row_text(screen, 1) == "│  no feeds running" + " " * (PANEL_W - 20) + "│"

    def test_a_narrow_panel_keeps_a_minimum_bar_and_cuts_the_text(self):
        screen = draw_feeds({"iMessage": status(stored=123456789)}, width=30)
        line = row_text(screen, 1)
        assert line.count("█") == DashboardTab.FEED_MIN_BAR
        assert line.endswith("... │") and len(line) == 30

    def test_more_feeds_than_rows_are_clipped_to_the_box(self):
        screen = draw_feeds({f"f{i}": status() for i in range(6)})
        assert row_text(screen, 4).startswith(f"│  f3 {SYMBOL_RUNNING}")
        assert row_text(screen, 5).startswith("╰")


@pytest.mark.unit
class TestIntegrationRows:
    """Disabled integrations leave the bullet list and share wrapped "Disabled: …" lines instead."""

    @staticmethod
    def status(name, state="ok"):
        return {"name": name, "state": state}

    def test_enabled_are_sorted_and_disabled_are_summarised(self):
        enabled, lines = DashboardTab._integration_rows([
            self.status("Ring", "disabled"), self.status("iMessage"), self.status("Arlo", "disabled"),
            self.status("Gmail"),
        ])
        assert [s["name"] for s in enabled] == ["Gmail", "iMessage"]
        assert lines == ["Disabled: Arlo, Ring"]

    def test_no_disabled_means_no_summary_lines(self):
        enabled, lines = DashboardTab._integration_rows([self.status("Gmail")])
        assert [s["name"] for s in enabled] == ["Gmail"]
        assert lines == []

    def test_a_long_disabled_list_wraps_inside_the_column(self):
        names = ["Arlo", "CalDAV", "Gmail", "Home Assistant", "Indigo", "Photos", "Reminders",
                 "Ring", "Self Improvement", "Sonos & Spotify", "Telegram", "Tesla", "X", "YR Weather"]
        _, lines = DashboardTab._integration_rows([self.status(n, "disabled") for n in names])
        assert len(lines) > 1
        assert all(len(line) <= DashboardTab.INTEGRATIONS_WIDTH for line in lines)
        assert lines[0].startswith("Disabled: ")
        assert all(not line.startswith(" ") for line in lines[1:])   # continuation lines sit flush left
        assert " ".join(lines) == "Disabled: " + ", ".join(names)

    def test_panel_height_counts_the_gap_and_summary_lines(self, monkeypatch):
        statuses = [self.status("Gmail"), self.status("Ring", "disabled")]
        monkeypatch.setattr("interfaces.console.tabs.DashboardTab.IntegrationMonitor.get_instance",
                            lambda: type("M", (), {"get_all_statuses": lambda self: statuses})())
        tab = make_tab()
        tab.console = type("C", (), {"outpost_latencies": [], "prompt_manager": None})()
        assert tab._status_panel_height() == max(1 + 1 + 1, DashboardTab.TIMING_ROWS, DashboardTab.RESOURCE_ROWS)
