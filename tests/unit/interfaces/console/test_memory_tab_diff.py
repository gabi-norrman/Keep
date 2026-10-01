from interfaces.console.tabs.BankTab import BankTab
from interfaces.console.widgets.DiffText import inline_diff


class TestExtractUpdatedMemory:
    def test_extracts_after_marker(self):
        content = "# Debug Log\nstuff\n## Updated Memory\nline1\nline2"
        assert BankTab._extract_updated_memory(content) == "line1\nline2"

    def test_strips_leading_newlines_after_marker(self):
        content = "## Updated Memory\n\n\nactual content"
        assert BankTab._extract_updated_memory(content) == "actual content"

    def test_no_marker_returns_full_content(self):
        content = "just regular content\nno marker here"
        assert BankTab._extract_updated_memory(content) == content


class TestInlineDiff:
    def test_single_word_change(self):
        old_spans, new_spans = inline_diff("the cat sat", "the dog sat")
        # "cat" -> "dog"
        assert len(old_spans) == 1
        assert len(new_spans) == 1
        assert "the cat sat"[old_spans[0][0]:old_spans[0][1]] == "cat"
        assert "the dog sat"[new_spans[0][0]:new_spans[0][1]] == "dog"

    def test_insertion_only(self):
        old_spans, new_spans = inline_diff("ab", "aXb")
        assert old_spans == []
        assert len(new_spans) == 1
        assert "aXb"[new_spans[0][0]:new_spans[0][1]] == "X"

    def test_deletion_only(self):
        old_spans, new_spans = inline_diff("aXb", "ab")
        assert len(old_spans) == 1
        assert "aXb"[old_spans[0][0]:old_spans[0][1]] == "X"
        assert new_spans == []


class TestBuildAnnotatedView:
    def test_identical_content(self):
        text = "line1\nline2"
        lines, types, highlights = BankTab._build_annotated_view(text, text)
        assert lines == ["line1", "line2"]
        assert types == ["normal", "normal"]
        assert highlights == [None, None]

    def test_added_lines(self):
        old = "line1"
        new = "line1\nline2"
        lines, types, highlights = BankTab._build_annotated_view(old, new)
        assert lines == ["line1", "line2"]
        assert types == ["normal", "added"]

    def test_removed_lines(self):
        old = "line1\nline2"
        new = "line1"
        lines, types, highlights = BankTab._build_annotated_view(old, new)
        assert lines == ["line1", "line2"]
        assert types == ["normal", "removed"]

    def test_replaced_lines_paired(self):
        old = "old line"
        new = "new line"
        lines, types, highlights = BankTab._build_annotated_view(old, new)
        assert lines == ["old line", "new line"]
        assert types == ["removed", "added"]
        # Inline highlights should exist for the changed parts
        assert highlights[0] is not None  # old spans
        assert highlights[1] is not None  # new spans

    def test_replace_unequal_block_sizes(self):
        old = "a\nb"
        new = "c"
        lines, types, highlights = BankTab._build_annotated_view(old, new)
        # First pair: a->c (replaced), then b (unpaired removal)
        assert types[0] == "removed"
        assert types[1] == "added"
        assert types[2] == "removed"
        assert lines[2] == "b"

def _make_tab_with_snapshots(tmp_path, n_dates):
    tab = BankTab()
    tab.log_dir = tmp_path
    for i in range(n_dates):
        (tmp_path / f"2026010{i + 1}_120000_memory.md").write_text("snapshot")
    return tab


class TestScanSnapshots:
    def test_missing_dir_clears_state(self, tmp_path):
        tab = BankTab()
        tab.log_dir = tmp_path / "does_not_exist"
        tab._scan_snapshots()
        assert tab._snapshot_dates == []
        assert tab._snapshot_files == {}

    def test_groups_files_by_date_newest_first(self, tmp_path):
        tab = BankTab()
        tab.log_dir = tmp_path
        (tmp_path / "20260101_120000_memory.md").write_text("a")
        (tmp_path / "20260101_130000_memory.md").write_text("b")
        (tmp_path / "20260102_120000_memory.md").write_text("c")
        tab._scan_snapshots()
        assert tab._snapshot_dates == ["20260102", "20260101"]
        assert len(tab._snapshot_files["20260101"]) == 2
        assert len(tab._snapshot_files["20260102"]) == 1

    def test_ignores_files_without_date_prefix(self, tmp_path):
        tab = BankTab()
        tab.log_dir = tmp_path
        (tmp_path / "20260101_120000_memory.md").write_text("a")
        (tmp_path / "notes_memory.md").write_text("b")
        tab._scan_snapshots()
        assert tab._snapshot_dates == ["20260101"]


class TestNavigateHistory:
    def test_no_snapshots_stays_live(self, tmp_path):
        tab = BankTab()
        tab.log_dir = tmp_path  # empty dir
        tab._navigate_history(-1)
        assert tab._history_index is None

    def test_older_from_live_enters_newest_snapshot(self, tmp_path):
        tab = _make_tab_with_snapshots(tmp_path, 2)
        assert tab._history_index is None
        tab._navigate_history(-1)
        assert tab._history_index == 0

    def test_older_advances_and_stops_at_oldest(self, tmp_path):
        tab = _make_tab_with_snapshots(tmp_path, 2)
        tab._navigate_history(-1)  # -> 0
        tab._navigate_history(-1)  # -> 1 (oldest of two)
        assert tab._history_index == 1
        tab._navigate_history(-1)  # clamped at oldest
        assert tab._history_index == 1

    def test_newer_walks_back_to_live(self, tmp_path):
        tab = _make_tab_with_snapshots(tmp_path, 2)
        tab._history_index = 1
        tab._navigate_history(1)  # -> 0
        assert tab._history_index == 0
        tab._navigate_history(1)  # -> live
        assert tab._history_index is None

    def test_newer_while_live_is_noop(self, tmp_path):
        tab = _make_tab_with_snapshots(tmp_path, 2)
        assert tab._history_index is None
        tab._navigate_history(1)
        assert tab._history_index is None
