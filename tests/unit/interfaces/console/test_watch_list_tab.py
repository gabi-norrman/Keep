"""WatchListTab: every open item gets a state — what it needs and when its next check falls —
and the board lists them most urgent first."""
from datetime import datetime, timedelta

import pytest

from assistant import WatchList
from common.vault.Redactor import Redactor
from common.vault.Vault import Vault
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs import WatchListTab as tab_module
from interfaces.console.tabs.TabView import SUB_CONTENT_TOP
from interfaces.console.tabs.WatchListTab import (CHECK_DUE, ON_TRACK, OPEN, OVERDUE, PAST_DUE, POLLED, STALE, WAITING,
                                                  WatchListTab, assess, span)

NOW = datetime(2026, 3, 10, 12, 0)


def item(name="parcel", every="", checked: timedelta = None, due_in: int = None, updated_days_ago=0):
    day = lambda offset: (NOW + timedelta(days=offset)).date().isoformat()
    return WatchList.Item(name, "order 4711", "" if due_in is None else day(due_in), day(-30), day(-updated_days_ago),
                          every, (NOW - checked).strftime("%Y-%m-%d %H:%M") if checked is not None else "")


@pytest.mark.unit
class TestAssess:
    @pytest.mark.parametrize("entry,kind", [
        (item(every="6h", checked=timedelta(hours=2)), ON_TRACK),
        (item(every="6h", checked=timedelta(hours=7)), CHECK_DUE),
        (item(every="6h", checked=timedelta(hours=10)), OVERDUE),
        (item(every="6h"), CHECK_DUE),
        (item(every="6h", checked=timedelta(hours=1), due_in=-1), PAST_DUE),
        (item(every="weekly", checked=timedelta(days=1), updated_days_ago=WatchList.STALE_AFTER_DAYS), STALE),
        (item(every="when it rains", checked=timedelta(hours=1)), POLLED),
        (item(due_in=5), WAITING),
        (item(due_in=0), WAITING),
        (item(), OPEN),
    ])
    def test_an_item_gets_the_state_its_cadence_and_dates_put_it_in(self, entry, kind):
        assert assess(entry, NOW).kind is kind

    def test_the_next_check_is_one_cadence_after_the_last(self):
        entry = assess(item(every="6h", checked=timedelta(hours=2)), NOW)
        assert entry.next_check == NOW + timedelta(hours=4)
        assert entry.fraction == pytest.approx(1 / 3)

    def test_an_item_without_a_cadence_has_no_next_check(self):
        entry = assess(item(due_in=5, checked=timedelta(hours=2)), NOW)
        assert entry.next_check is None and entry.fraction is None and entry.due_days == 5

    def test_the_most_urgent_sorts_first(self):
        entries = [assess(i, NOW) for i in (
            item("on-track-later", every="1d", checked=timedelta(hours=1)),
            item("waiting", due_in=3),
            item("on-track-sooner", every="1d", checked=timedelta(hours=20)),
            item("late", every="6h", checked=timedelta(hours=20)),
            item("past", due_in=-2),
        )]
        assert [e.item.name for e in sorted(entries, key=lambda e: e.urgency)] == \
            ["past", "late", "on-track-sooner", "on-track-later", "waiting"]


@pytest.mark.unit
class TestSpan:
    @pytest.mark.parametrize("delta,text", [
        (timedelta(seconds=20), "<1m"), (timedelta(minutes=42), "42m"), (timedelta(hours=3), "3h"),
        (timedelta(hours=3, minutes=50), "3h 50m"), (timedelta(days=2, hours=4), "2d 4h"),
        (timedelta(days=12, hours=5), "12d"), (-timedelta(minutes=5), "5m"),
    ])
    def test_a_span_reads_in_its_two_largest_units(self, delta, text):
        assert span(delta) == text


WIDTH, HEIGHT = 120, 40


@pytest.mark.unit
class TestBoard:
    @pytest.fixture(autouse=True)
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(WatchList, "WATCHLIST_DIR", tmp_path / "watchlist")
        monkeypatch.setattr(WatchList, "LOG_DIR", tmp_path / "log")
        monkeypatch.setattr(WatchListTab, "now", staticmethod(lambda: NOW))
        WatchList.WATCHLIST_DIR.mkdir()
        for entry in (item("sofa-delivery", every="1d", checked=timedelta(hours=1)),
                      item("festival-tickets", every="6h", checked=timedelta(hours=20))):
            (WatchList.WATCHLIST_DIR / f"{entry.name}.md").write_text(entry.text().replace("order 4711", f"note of {entry.name}"))
        self.tab = WatchListTab(Redactor(Vault(tmp_path / "vault.bin", "test")))

    def rows(self, width=WIDTH) -> list[str]:
        screen = TUIScreen(width, HEIGHT)
        self.tab.draw(screen, 0)
        return [screen.row_text(1, y, width).rstrip() for y in range(SUB_CONTENT_TOP, HEIGHT)]

    def test_the_late_item_is_listed_above_the_one_on_schedule(self):
        rows = self.rows()
        rows = rows[next(i for i, row in enumerate(rows) if "Every" in row):]
        late = next(i for i, row in enumerate(rows) if "festival-tickets" in row and OVERDUE.label in row)
        on_track = next(i for i, row in enumerate(rows) if "sofa-delivery" in row and ON_TRACK.label in row)
        assert late < on_track
        assert "14h late" in rows[late] and "in 23h" in rows[on_track]

    def test_the_most_urgent_item_is_selected_and_down_moves_to_the_next(self):
        assert "note of festival-tickets" in "\n".join(self.rows())
        assert self.tab.handle_input("down")
        text = "\n".join(self.rows())
        assert "note of sofa-delivery" in text and "note of festival-tickets" not in text

    def test_the_selection_stops_at_the_ends(self):
        self.rows()
        for _ in range(5):
            self.tab.handle_input("down")
        assert "note of sofa-delivery" in "\n".join(self.rows())
        for _ in range(5):
            self.tab.handle_input("up")
        assert "note of festival-tickets" in "\n".join(self.rows())

    def test_a_wide_screen_puts_the_selected_item_beside_the_table(self):
        rows = self.rows(WatchListTab.SIDE_BY_SIDE_MIN + 40)
        reason = assess(item("festival-tickets", every="6h", checked=timedelta(hours=20)), NOW).reason
        assert any("Every" in row and reason in row for row in rows), "the table's titles and the item's state share a row"
        assert not any("Every" in row and reason in row for row in self.rows())

    def test_a_deleted_selection_falls_back_to_the_first_item(self):
        self.rows()
        WatchList.delete("festival-tickets")
        assert "note of sofa-delivery" in "\n".join(self.rows())

    @pytest.mark.parametrize("width,height", [(60, 12), (30, 8), (250, 14)])
    def test_a_cramped_screen_still_draws(self, width, height):
        self.tab.draw(TUIScreen(width, height), 0)

    def test_the_longest_lateness_is_shown_whole(self):
        late = item("router-firmware", every="1d", checked=timedelta(days=1, hours=23, minutes=59))
        (WatchList.WATCHLIST_DIR / "router-firmware.md").write_text(late.text())
        for width in (WIDTH, WatchListTab.SIDE_BY_SIDE_MIN + 40):
            assert any("router-firmware" in row and "23h 59m late" in row for row in self.rows(width))
