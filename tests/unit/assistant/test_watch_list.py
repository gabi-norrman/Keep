"""WatchList: one file per tracked item under a directory, answering to its name; the note is
the current state, deleting removes the file, and every replaced or deleted version lands in
the log directory."""
import os
from datetime import date, timedelta

import pytest

from assistant import WatchList

TODAY = date(2026, 3, 10)


@pytest.fixture
def wl(tmp_path):
    return tmp_path / "watchlist"


@pytest.fixture
def log(tmp_path):
    return tmp_path / "log"


def log_texts(log) -> list[str]:
    return [p.read_text() for p in sorted(log.glob("*.md"))] if log.is_dir() else []


def backdate(directory, name: str, field: str, day: date):
    """Rewrite one header date of an item's file, as if it had been written on `day`."""
    path = directory / f"{name}.md"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(f"{field}: {day.isoformat()}" if l.startswith(f"{field}:") else l for l in lines) + "\n")


@pytest.mark.unit
class TestLifecycle:
    def test_saved_items_are_read_back_from_their_files(self, wl, log):
        WatchList.save("Parcel from shop.example", "order 4711, courier, evening", "2026-03-12", wl, log)
        WatchList.save("Dentist for Charlotte", "12:30 at the clinic", None, wl, log)
        items = {i.name: (i.note, i.due) for i in WatchList.items(wl)}
        assert items == {
            "parcel-from-shop-example": ("order 4711, courier, evening", "2026-03-12"),
            "dentist-for-charlotte": ("12:30 at the clinic", ""),
        }
        assert len(list(wl.glob("*.md"))) == 2

    def test_items_come_oldest_first(self, wl, log):
        WatchList.save("alpha", "new", None, wl, log)
        WatchList.save("zulu", "old", None, wl, log)
        backdate(wl, "zulu", "Created", date.today() - timedelta(days=3))
        assert [i.name for i in WatchList.items(wl)] == ["zulu", "alpha"]

    def test_a_multi_line_note_survives_the_file(self, wl, log):
        WatchList.save("Invoice", "amount 1,200\n\nbankgiro 123-4567", None, wl, log)
        assert WatchList.items(wl)[0].note == "amount 1,200\n\nbankgiro 123-4567"

    def test_no_directory_means_no_items(self, wl):
        assert WatchList.items(wl) == []

    def test_saving_again_replaces_the_note_and_keeps_the_due_date(self, wl, log):
        WatchList.save("Parcel", "ordered", "2026-03-12", wl, log)
        updated = WatchList.save("Parcel", "shipped, tracking AB12", None, wl, log)
        assert updated.note == "shipped, tracking AB12"
        only, = WatchList.items(wl)
        assert (only.note, only.due) == ("shipped, tracking AB12", "2026-03-12")

    @pytest.mark.parametrize("spelling", ["tv delivery", "TV  Delivery", '"tv-delivery"', "`TV-delivery`", " Tv Delivery "])
    def test_another_spelling_of_the_name_updates_the_same_item(self, wl, log, spelling):
        WatchList.save("TV delivery", "ordered", "2026-03-12", wl, log)
        WatchList.save(spelling, "out for delivery", None, wl, log)
        only, = WatchList.items(wl)
        assert (only.name, only.note, only.due) == ("tv-delivery", "out for delivery", "2026-03-12")

    def test_saving_with_a_due_date_changes_it_and_a_dash_clears_it(self, wl, log):
        WatchList.save("Parcel", "ordered", "2026-03-12", wl, log)
        WatchList.save("Parcel", "delayed", "2026-03-15", wl, log)
        assert WatchList.get("Parcel", wl).due == "2026-03-15"
        WatchList.save("Parcel", "no date any more", "-", wl, log)
        assert WatchList.get("Parcel", wl).due == ""

    def test_an_update_keeps_the_day_the_item_was_created(self, wl, log):
        WatchList.save("Parcel", "ordered", None, wl, log)
        created = date.today() - timedelta(days=6)
        backdate(wl, "parcel", "Created", created)
        WatchList.save("Parcel", "shipped", None, wl, log)
        assert WatchList.get("Parcel", wl).created == created.isoformat()

    def test_an_update_leaves_the_previous_state_in_the_log(self, wl, log):
        WatchList.save("Parcel", "ordered at shop.example", None, wl, log)
        assert log_texts(log) == []
        WatchList.save("Parcel", "shipped", None, wl, log)
        texts = log_texts(log)
        assert len(texts) == 1
        assert "ordered at shop.example" in texts[0] and "shipped" not in texts[0]

    def test_delete_removes_the_file_and_logs_the_outcome(self, wl, log):
        WatchList.save("Parcel", "shipped", None, wl, log)
        deleted = WatchList.delete("parcel", "delivered to the door", wl, log)
        assert deleted.name == "parcel"
        assert WatchList.items(wl) == []
        assert list(wl.glob("*.md")) == []
        texts = log_texts(log)
        assert len(texts) == 1
        assert "shipped" in texts[0] and "delivered to the door" in texts[0]

    def test_deleting_an_unknown_item_does_nothing(self, wl, log):
        WatchList.save("Parcel", "shipped", None, wl, log)
        assert WatchList.delete("dentist", "x", wl, log) is None
        assert len(WatchList.items(wl)) == 1
        assert log_texts(log) == []

    def test_a_name_without_letters_or_digits_is_refused(self, wl, log):
        assert WatchList.save(" -- ", "note", None, wl, log) is None
        assert WatchList.get("", wl) is None
        assert WatchList.items(wl) == []

    def test_a_deleted_name_can_be_watched_afresh(self, wl, log):
        WatchList.save("Parcel", "first order", "2026-03-12", wl, log)
        WatchList.delete("Parcel", "delivered", wl, log)
        again = WatchList.save("Parcel", "second order", None, wl, log)
        assert (again.note, again.due) == ("second order", "")


@pytest.mark.unit
class TestStaleness:
    def item(self, due="", updated=TODAY):
        return WatchList.Item("parcel", "note", due, TODAY.isoformat(), updated.isoformat())

    def test_a_fresh_item_is_not_stale(self):
        assert WatchList.staleness(self.item(due=TODAY.isoformat()), TODAY) == ""

    def test_due_yesterday_is_not_yet_stale_two_days_is(self):
        assert WatchList.staleness(self.item(due=(TODAY - timedelta(days=1)).isoformat()), TODAY) == ""
        assert "STALE" in WatchList.staleness(self.item(due=(TODAY - timedelta(days=2)).isoformat()), TODAY)

    def test_a_due_date_with_a_time_counts_by_its_date(self):
        due = f"{(TODAY - timedelta(days=5)).isoformat()} 17:45"
        assert "5 days" in WatchList.staleness(self.item(due=due), TODAY)

    def test_a_free_text_due_is_never_overdue(self):
        assert WatchList.staleness(self.item(due="early next month"), TODAY) == ""

    def test_untouched_for_two_weeks_is_stale(self):
        assert WatchList.staleness(self.item(updated=TODAY - timedelta(days=13)), TODAY) == ""
        assert "STALE" in WatchList.staleness(self.item(updated=TODAY - timedelta(days=14)), TODAY)


@pytest.mark.unit
class TestRender:
    def test_empty_list_says_so(self, wl):
        assert WatchList.render(wl) == WatchList.EMPTY

    def test_each_open_item_carries_its_name_due_and_note(self, wl, log):
        WatchList.save("Parcel from shop.example", "order 4711", "2026-03-12", wl, log)
        WatchList.save("Dentist for Charlotte", "12:30", None, wl, log)
        blocks = {b.splitlines()[0].split("`")[1]: b for b in WatchList.render(wl, date.today()).split("\n\n")}
        parcel, dentist = blocks["parcel-from-shop-example"], blocks["dentist-for-charlotte"]
        assert "2026-03-12" in parcel and "order 4711" in parcel
        assert "12:30" in dentist
        assert "due" not in dentist.splitlines()[0]

    def test_a_stale_item_is_marked_and_a_fresh_one_is_not(self, wl, log):
        WatchList.save("Old parcel", "x", (date.today() - timedelta(days=9)).isoformat(), wl, log)
        WatchList.save("New parcel", "x", (date.today() + timedelta(days=2)).isoformat(), wl, log)
        blocks = WatchList.render(wl).split("\n\n")
        old = next(b for b in blocks if "old-parcel" in b)
        new = next(b for b in blocks if "new-parcel" in b)
        assert "STALE" in old and "STALE" not in new

    def test_an_item_nobody_touched_is_marked(self, wl, log):
        WatchList.save("Forgotten", "x", None, wl, log)
        backdate(wl, "forgotten", "Updated", date.today() - timedelta(days=30))
        assert "STALE" in WatchList.render(wl)

    def test_a_deleted_item_is_gone(self, wl, log):
        WatchList.save("Parcel", "x", None, wl, log)
        WatchList.delete("Parcel", "done", wl, log)
        assert WatchList.render(wl) == WatchList.EMPTY


@pytest.mark.unit
class TestVersion:
    def test_version_moves_on_save_update_and_delete(self, wl, log):
        empty = WatchList.version(wl)
        WatchList.save("Parcel", "ordered", None, wl, log)
        added = WatchList.version(wl)
        assert added != empty
        # A same-day rewrite differs only by mtime: pin the old one so a coarse clock cannot hide it.
        os.utime(wl / "parcel.md", ns=(1, 1))
        before_update = WatchList.version(wl)
        WatchList.save("Parcel", "shipped", None, wl, log)
        updated = WatchList.version(wl)
        assert updated != before_update
        WatchList.delete("Parcel", "", wl, log)
        assert WatchList.version(wl) != updated

    def test_version_is_stable_when_nothing_changes(self, wl, log):
        WatchList.save("Parcel", "x", None, wl, log)
        assert WatchList.version(wl) == WatchList.version(wl)


@pytest.mark.unit
class TestRun:
    def test_save_watch_takes_name_note_and_due(self, wl, log):
        assert WatchList.run("save_watch", ["Parcel", "order 4711", "2026-03-12"], wl, log) == ""
        item = WatchList.items(wl)[0]
        assert (item.name, item.note, item.due) == ("parcel", "order 4711", "2026-03-12")

    def test_save_watch_without_a_due_date_keeps_the_one_it_has(self, wl, log):
        WatchList.run("save_watch", ["Parcel", "ordered", "2026-03-12"], wl, log)
        assert WatchList.run("save_watch", ["parcel", "shipped"], wl, log) == ""
        assert WatchList.run("save_watch", ["parcel", "at the depot", ""], wl, log) == ""
        item, = WatchList.items(wl)
        assert (item.note, item.due) == ("at the depot", "2026-03-12")

    def test_save_watch_without_a_name_or_a_note_is_refused(self, wl, log):
        assert WatchList.run("save_watch", [], wl, log)
        assert WatchList.run("save_watch", ["Parcel"], wl, log)
        assert WatchList.run("save_watch", ["Parcel", "  "], wl, log)
        assert WatchList.run("save_watch", ["  ", "a note"], wl, log)
        assert WatchList.items(wl) == []

    def test_an_unknown_name_is_an_error_that_names_the_open_ones(self, wl, log):
        WatchList.save("Parcel", "x", None, wl, log)
        WatchList.save("Dentist", "x", None, wl, log)
        failure = WatchList.run("delete_watch", ["plumber", "done"], wl, log)
        assert "plumber" in failure and "parcel" in failure and "dentist" in failure
        assert len(WatchList.items(wl)) == 2

    def test_delete_without_a_name_is_an_error(self, wl, log):
        assert WatchList.run("delete_watch", [], wl, log)
        assert WatchList.run("delete_watch", [" "], wl, log)

    def test_delete_joins_a_comma_split_outcome(self, wl, log):
        WatchList.save("Parcel", "x", None, wl, log)
        assert WatchList.run("delete_watch", ["Parcel", "delivered", "left at the door"], wl, log) == ""
        assert "delivered, left at the door" in log_texts(log)[0]

    def test_a_foreign_command_is_refused(self, wl, log):
        assert WatchList.run("save_procedure", ["a", "b", "c"], wl, log)
        assert WatchList.run("watch_close", ["parcel"], wl, log)
