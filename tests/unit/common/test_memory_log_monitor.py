from datetime import date
from unittest.mock import Mock, patch

import pytest

from common.MemoryLogMonitor import MemoryLogMonitor


def _make_monitor():
    tab = Mock()
    tab.memory_log_sizes = []
    tab.memory_log_error = ""
    tab.memory_word_count = 0
    return MemoryLogMonitor(tab, fetch_interval=0), tab


@pytest.fixture(autouse=True)
def _no_real_memory_bank(tmp_path):
    with patch.object(MemoryLogMonitor, "MEMORY_FILE", tmp_path / "bank" / "memory.md"):
        yield


def _snapshot_body(new_size_chars: int, extra_bytes: int = 0) -> str:
    body = (
        "# Memory Debug Log\n\n"
        "- Timestamp: 2026-01-01 04:00:00\n"
        "- Interactions: 50\n"
        "- Calendar events: 5\n"
        "- Old size: 4000 chars\n"
        f"- New size: {new_size_chars} chars\n"
        "- LLM duration: 10.0s\n\n"
        "---\n\n"
    )
    if extra_bytes > 0:
        body += "x" * extra_bytes
    return body


def _write(dir_path, name, content):
    p = dir_path / name
    p.write_text(content)
    return p


@pytest.mark.unit
class TestMemoryLogMonitorScan:

    def test_parses_new_size_header_not_file_size(self, tmp_path):
        # File is huge but header says memory is small — must report header value
        _write(tmp_path, "20260101_040000_abc123_memory.md",
               _snapshot_body(new_size_chars=5120, extra_bytes=200_000))

        monitor, tab = _make_monitor()
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path):
            monitor._scan()

        assert len(tab.memory_log_sizes) == 1
        day, kb = tab.memory_log_sizes[0]
        assert day == date(2026, 1, 1)
        assert round(kb, 2) == 5.00  # 5120 / 1024

    def test_parses_multiple_files_and_sorts_by_date(self, tmp_path):
        _write(tmp_path, "20260101_040000_abc123_memory.md", _snapshot_body(1024))
        _write(tmp_path, "20260103_040000_def456_memory.md", _snapshot_body(2048))
        _write(tmp_path, "20260102_040000_aaa999_memory.md", _snapshot_body(3072))

        monitor, tab = _make_monitor()
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path):
            monitor._scan()

        assert [d for d, _ in tab.memory_log_sizes] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
        assert [round(kb, 1) for _, kb in tab.memory_log_sizes] == [1.0, 3.0, 2.0]
        assert tab.memory_log_error == ""

    def test_missing_directory_sets_error_and_empties_data(self, tmp_path):
        monitor, tab = _make_monitor()
        tab.memory_log_sizes = [(date(2026, 1, 1), 5.0)]
        missing = tmp_path / "does_not_exist"
        with patch.object(MemoryLogMonitor, "LOG_DIR", missing):
            monitor._scan()

        assert tab.memory_log_sizes == []
        assert "not found" in tab.memory_log_error.lower()

    def test_skips_malformed_filenames(self, tmp_path):
        _write(tmp_path, "20260101_040000_abc_memory.md", _snapshot_body(1024))
        _write(tmp_path, "not_a_memory_file.txt", "junk")
        _write(tmp_path, "memory.md", "junk")
        _write(tmp_path, "99999999_040000_abc_memory.md", _snapshot_body(1024))  # bad date

        monitor, tab = _make_monitor()
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path):
            monitor._scan()

        assert len(tab.memory_log_sizes) == 1
        assert tab.memory_log_sizes[0][0] == date(2026, 1, 1)

    def test_skips_files_without_new_size_header(self, tmp_path):
        _write(tmp_path, "20260101_040000_abc123_memory.md",
               "# Memory Debug Log\n\nno header here, just text\n")
        _write(tmp_path, "20260102_040000_def456_memory.md", _snapshot_body(2048))

        monitor, tab = _make_monitor()
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path):
            monitor._scan()

        assert len(tab.memory_log_sizes) == 1
        assert tab.memory_log_sizes[0][0] == date(2026, 1, 2)

    def test_multiple_files_same_day_keeps_largest_memory(self, tmp_path):
        _write(tmp_path, "20260101_040000_aaaaaa_memory.md", _snapshot_body(1024))
        _write(tmp_path, "20260101_120000_bbbbbb_memory.md", _snapshot_body(5120))
        _write(tmp_path, "20260101_180000_cccccc_memory.md", _snapshot_body(3072))

        monitor, tab = _make_monitor()
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path):
            monitor._scan()

        assert len(tab.memory_log_sizes) == 1
        assert tab.memory_log_sizes[0][0] == date(2026, 1, 1)
        assert round(tab.memory_log_sizes[0][1], 2) == 5.0

    def test_counts_words_of_live_memory_bank(self, tmp_path):
        bank = tmp_path / "memory.md"
        bank.write_text("# Long-term memory bank\n\n---\n- Marcus likes  tea\n- Charlotte plays piano\n")

        monitor, tab = _make_monitor()
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path), \
             patch.object(MemoryLogMonitor, "MEMORY_FILE", bank):
            monitor._scan()

        assert tab.memory_word_count == 9  # markdown markers are not words

    def test_missing_memory_bank_counts_zero_words(self, tmp_path):
        monitor, tab = _make_monitor()
        tab.memory_word_count = 42
        with patch.object(MemoryLogMonitor, "LOG_DIR", tmp_path):
            monitor._scan()

        assert tab.memory_word_count == 0
