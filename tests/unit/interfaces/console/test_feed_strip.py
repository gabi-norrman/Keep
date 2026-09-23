"""FeedStrip: the footer's feed chips — a dot per feed, a one-cell bottom-up bar with the ETA
for one catching up, the once-a-second rebuild — and the ChatPane footer drawing them at the right."""
from unittest.mock import patch

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.widgets.ChatPane import ChatPane
from interfaces.console.widgets.FeedStrip import (BAR_EMPTY_DIMMING, COLOR_STARTING, PAUSED_DIMMING, SYMBOL_FAILED, SYMBOL_PAUSED,
                                                  SYMBOL_RUNNING, FeedChip, FeedStrip, bar_block, chip_for, dimmed, format_eta,
                                                  strip_width)

WIDTH, HEIGHT = 100, 12

FILL = dimmed(Theme.COLOR_GREEN_FG_BRIGHTER)
BOX = dimmed(Theme.COLOR_GREEN_FG_BRIGHTER, BAR_EMPTY_DIMMING)


def status(state="live", **extra) -> dict:
    base = {"state": state, "paused": False, "progress": None, "remaining": None, "eta_seconds": None, "last_error": None}
    base.update(extra)
    return base


def footer(pane: ChatPane, width: int = WIDTH) -> tuple[str, TUIScreen]:
    screen = TUIScreen(width, HEIGHT)
    screen.clear_buffer()
    pane.draw(screen, 0, 1, 1, width, HEIGHT)
    return "".join(c.char for c in screen.next_frame[HEIGHT - 1]).rstrip(), screen


class TestBarBlock:
    def test_fills_in_eighths_from_the_bottom(self):
        assert [bar_block(f) for f in (0.0, 0.1, 0.34, 0.5, 0.99, 1.0)] == [" ", " ", "▂", "▄", "▇", "█"]

    def test_clamps(self):
        assert (bar_block(-1), bar_block(2)) == (" ", "█")


class TestFormatEta:
    def test_one_rounded_unit(self):
        assert [format_eta(s) for s in (45, 13 * 60 + 20, 13 * 3600, 47 * 3600, 3 * 86400)] == ["45s", "13m", "13h", "47h", "3d"]


class TestChipFor:
    def test_live_is_a_green_dot_and_the_name(self):
        assert chip_for("iMessage", status()) == FeedChip(SYMBOL_RUNNING, FILL, "iMessage")

    def test_catching_up_fills_the_bar_and_shows_the_eta(self):
        chip = chip_for("iCloud Photos", status("catching_up", progress=0.34, remaining=120, eta_seconds=13 * 3600))
        assert chip == FeedChip("▂", FILL, "Photos ETA: 13h", BOX)

    def test_catching_up_without_an_eta_shows_what_is_left(self):
        assert chip_for("Indigo", status("catching_up", remaining=1203)).label == "Indigo 1203 left"

    def test_catching_up_with_nothing_to_report_is_the_name_over_an_empty_box(self):
        assert chip_for("Indigo", status("catching_up")) == FeedChip(" ", FILL, "Indigo", BOX)

    def test_failed_and_stopped_are_a_red_cross_without_a_box(self):
        for state in ("failed", "stopped"):
            assert chip_for("Indigo", status(state, last_error="boom")) == FeedChip(SYMBOL_FAILED, dimmed(Theme.COLOR_RED_FG), "Indigo")

    def test_starting_is_a_dim_dot(self):
        assert chip_for("Telegram", status("starting")) == FeedChip(SYMBOL_RUNNING, dimmed(COLOR_STARTING), "Telegram")

    def test_paused_hollows_the_dot_and_keeps_the_colour(self):
        assert chip_for("iMessage", status(paused=True)) == FeedChip(SYMBOL_PAUSED, FILL, "iMessage")

    def test_paused_catch_up_keeps_the_bar_and_fades_it(self):
        chip = chip_for("iCloud Photos", status("catching_up", progress=0.5, eta_seconds=90, paused=True))
        assert chip == FeedChip("▄", dimmed(FILL, PAUSED_DIMMING), "Photos ETA: 2m", dimmed(BOX, PAUSED_DIMMING))

    def test_paused_never_hides_a_failure(self):
        assert chip_for("Indigo", status("failed", paused=True)).symbol == SYMBOL_FAILED


class TestFeedStrip:
    def test_builds_one_chip_per_running_feed_in_registry_order(self):
        statuses = {"iMessage": status(), "iCloud Photos": status("catching_up", progress=0.34)}
        with patch("assistant.integration.FeedRunner.Feeds.get_instance") as feeds:
            feeds.return_value.names.return_value = list(statuses)
            feeds.return_value.status.side_effect = statuses.get
            chips = FeedStrip().chips(now=0.0)
        assert [(c.symbol, c.label) for c in chips] == [(SYMBOL_RUNNING, "iMessage"), ("▂", "Photos")]

    def test_rebuilds_once_a_second(self):
        strip = FeedStrip()
        with patch.object(FeedStrip, "_build", return_value=[]) as build:
            strip.chips(now=0.0)
            strip.chips(now=0.5)
            assert build.call_count == 1
            strip.chips(now=1.0)
            assert build.call_count == 2

    def test_width_counts_the_separators(self):
        chips = [FeedChip("●", FILL, "iMessage"), FeedChip("▂", FILL, "Indigo", BOX)]
        assert strip_width(chips) == len("Feeds  ● iMessage · ▂ Indigo")


class TestFooter:
    def test_chips_sit_at_the_right_of_the_hints(self):
        pane = ChatPane()
        pane.footer_chips = [chip_for("iMessage", status()),
                             chip_for("iCloud Photos", status("catching_up", progress=0.34, eta_seconds=13 * 3600))]
        line, screen = footer(pane)
        assert line.endswith("Feeds  ● iMessage · ▂ Photos ETA: 13h")
        assert line.startswith(ChatPane.FOOTER)
        bar = screen.next_frame[HEIGHT - 1][line.index("▂")]
        assert (bar.style.fg, bar.style.bg) == (FILL, BOX)
        label = screen.next_frame[HEIGHT - 1][line.index("Photos")]
        assert (label.style.fg, label.style.bg) == (ChatPane.COLOR_DIM, None), "the box ends with the bar"

    def test_the_dots_and_the_cross_have_no_box(self):
        pane = ChatPane()
        pane.footer_chips = [chip_for("iMessage", status()), chip_for("Indigo", status("failed"))]
        line, screen = footer(pane)
        dot = screen.next_frame[HEIGHT - 1][line.index(SYMBOL_RUNNING)]
        assert (dot.style.fg, dot.style.bg) == (FILL, None)
        cross = screen.next_frame[HEIGHT - 1][line.index(SYMBOL_FAILED)]
        assert (cross.style.fg, cross.style.bg) == (dimmed(Theme.COLOR_RED_FG), None)

    def test_notice_replaces_the_chips(self):
        pane = ChatPane()
        pane.footer_chips = [chip_for("iMessage", status())]
        pane.footer_notice = "WAITING FOR USER"
        line, _ = footer(pane)
        assert line.endswith("WAITING FOR USER") and "iMessage" not in line

    def test_narrow_pane_clips_the_hints_before_the_chips(self):
        pane = ChatPane()
        pane.footer_chips = [chip_for("iMessage", status()), chip_for("Indigo", status())]
        line, _ = footer(pane, width=30)
        assert line.endswith("Feeds  ● iMessage · ● Indigo")
        assert len(line) <= 30

    def test_strip_wider_than_the_pane_drops_its_last_chips(self):
        pane = ChatPane()
        pane.footer_chips = [chip_for("iMessage", status()), chip_for("Indigo", status())]
        line, _ = footer(pane, width=20)
        assert line.endswith("Feeds  ● iMessage") and "Indigo" not in line
