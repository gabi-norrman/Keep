"""CacheHitLogMonitor: the per-day prompt-cache average the dashboard charts, scanned from
the appended-forever server log in one startup pass and incremental daily passes."""
import threading
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from common.CacheHitLogMonitor import CacheHitLogMonitor

TODAY = date(2026, 3, 7)


def log_line(day: str, pct: int, read: int = 1200, total: int = 1500) -> bytes:
    """A cache line as `common.logging` writes it to floxy.out: level, timestamp and thread
    wrapped in colour codes, then the Claude cache message."""
    return (f"\x1b[2m\x1b[90mT\x1b[38;2;80;80;80m│\x1b[2m\x1b[90m{day} 09:40:17.051 +0.000\x1b[38;2;80;80;80m │ "
            f"\x1b[2m\x1b[90mmain            \x1b[38;2;80;80;80m │ \x1b[0m\x1b[2m\x1b[37m  Claude cache: {read} read, "
            f"0 created (0 5m + 0 1h), {total - read} uncached (total {total}, {pct}% cached)\n").encode()


OTHER_LINES = ("\x1b[2m\x1b[90mI\x1b[38;2;80;80;80m│\x1b[2m\x1b[90m2026-03-05 09:40:17.000 +0.000 │ main │ \x1b[0m"
               "Sending to Claude (with cache)...\n"
               "I│2026-03-05 09:40:18.000 │ main │ Number of unique responses in cache: 3\n").encode()


def make_monitor(tmp_path, monkeypatch, **overrides):
    tab = Mock()
    tab.cache_daily, tab.cache_daily_error = [], ""
    log = tmp_path / "floxy.out"
    monkeypatch.setattr(CacheHitLogMonitor, "LOG_FILE", log)
    for name, value in overrides.items():
        monkeypatch.setattr(CacheHitLogMonitor, name, value)
    return CacheHitLogMonitor(tab, scan_interval=0), tab, log


def scan(monitor):
    monitor._scan(threading.Event())


@pytest.mark.unit
class TestTheLogLine:
    def test_the_coloured_line_yields_its_day_and_percentage__contract_with_floxy_out(self, tmp_path, monkeypatch):
        monitor, _, _ = make_monitor(tmp_path, monkeypatch)
        assert monitor._take([log_line("2026-03-05", 80), *OTHER_LINES.split(b"\n")]) == 1
        assert dict(monitor._sums) == {date(2026, 3, 5): [80, 1]}

    def test_a_cache_line_of_another_shape_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        monitor, _, _ = make_monitor(tmp_path, monkeypatch)
        assert monitor._take([b"Claude cache: 5 read, 5 uncached (total 10, 50% cached)  <- no timestamp"]) == 0
        assert not monitor._sums


@pytest.mark.unit
class TestDailyAverages:
    def test_a_day_is_the_mean_of_its_requests_and_a_day_without_one_is_a_gap(self):
        sums = {date(2026, 3, 5): [150, 2], date(2026, 3, 7): [40, 1]}
        assert CacheHitLogMonitor.daily_averages(sums, TODAY) == [
            (date(2026, 3, 5), 75.0), (date(2026, 3, 6), None), (date(2026, 3, 7), 40.0)]

    def test_the_series_runs_through_today_even_when_today_has_no_request_yet(self):
        series = CacheHitLogMonitor.daily_averages({date(2026, 3, 5): [90, 1]}, TODAY)
        assert series[-1] == (TODAY, None) and len(series) == 3

    def test_the_series_reaches_no_further_back_than_max_days(self):
        ancient = TODAY - timedelta(days=CacheHitLogMonitor.MAX_DAYS + 30)
        series = CacheHitLogMonitor.daily_averages({ancient: [50, 1], TODAY: [10, 1]}, TODAY)
        assert len(series) == CacheHitLogMonitor.MAX_DAYS
        assert series[-1] == (TODAY, 10.0)

    def test_no_requests_is_an_empty_series(self):
        assert CacheHitLogMonitor.daily_averages({}, TODAY) == []
        assert CacheHitLogMonitor.daily_averages({TODAY: [0, 0]}, TODAY) == []


@pytest.mark.unit
class TestScanning:
    def test_the_first_scan_averages_every_day_in_the_log(self, tmp_path, monkeypatch):
        monitor, tab, log = make_monitor(tmp_path, monkeypatch)
        today, yesterday = date.today(), date.today() - timedelta(days=1)
        log.write_bytes(OTHER_LINES + log_line(str(yesterday), 60) + log_line(str(yesterday), 80)
                        + log_line(str(today), 30))
        scan(monitor)
        assert tab.cache_daily == [(yesterday, 70.0), (today, 30.0)]
        assert tab.cache_daily_error == ""

    def test_a_rescan_reads_only_what_was_appended(self, tmp_path, monkeypatch):
        monitor, tab, log = make_monitor(tmp_path, monkeypatch)
        today = date.today()
        log.write_bytes(log_line(str(today), 100))
        scan(monitor)
        with log.open("ab") as f:
            f.write(log_line(str(today), 0))
        scan(monitor)
        assert tab.cache_daily == [(today, 50.0)], "a line counted twice would pull the mean off"
        assert monitor._offset == log.stat().st_size

    def test_a_pass_stops_at_the_size_it_started_with(self, tmp_path, monkeypatch):
        """The monitor's own trace lines are appended to the log while a pass reads it; a pass
        that read on to the end would chase them and keep going for as long as it wrote."""
        monitor, tab, log = make_monitor(tmp_path, monkeypatch, CHUNK_BYTES=37, CHUNK_PAUSE=0)
        today = date.today()
        log.write_bytes(log_line(str(today), 40) + log_line(str(today), 60))
        size_at_start = log.stat().st_size

        def take_and_append(lines, _take=monitor._take):
            with log.open("ab") as f:
                f.write(log_line(str(today), 0))
            return _take(lines)

        monkeypatch.setattr(monitor, "_take", take_and_append)
        scan(monitor)
        assert monitor._offset == size_at_start
        assert tab.cache_daily == [(today, 50.0)], "what was appended during the pass is the next pass's"

    def test_a_line_cut_by_a_chunk_boundary_is_still_counted(self, tmp_path, monkeypatch):
        monitor, tab, log = make_monitor(tmp_path, monkeypatch, CHUNK_BYTES=37, CHUNK_PAUSE=0)
        today = date.today()
        log.write_bytes(b"".join(log_line(str(today), pct) for pct in (10, 20, 30, 40)))
        scan(monitor)
        assert tab.cache_daily == [(today, 25.0)]

    def test_a_shrunk_log_is_read_from_the_start(self, tmp_path, monkeypatch):
        monitor, tab, log = make_monitor(tmp_path, monkeypatch)
        today = date.today()
        log.write_bytes(log_line(str(today), 90) + log_line(str(today), 90) + log_line(str(today), 90))
        scan(monitor)
        log.write_bytes(log_line(str(today), 10))
        scan(monitor)
        assert tab.cache_daily == [(today, 10.0)]

    def test_a_log_with_nothing_new_keeps_the_series(self, tmp_path, monkeypatch):
        monitor, tab, log = make_monitor(tmp_path, monkeypatch)
        today = date.today()
        log.write_bytes(log_line(str(today), 42))
        scan(monitor)
        tab.cache_daily = []
        scan(monitor)
        assert tab.cache_daily == [(today, 42.0)]

    def test_a_missing_log_is_reported_not_raised(self, tmp_path, monkeypatch):
        monitor, tab, _ = make_monitor(tmp_path, monkeypatch)
        scan(monitor)
        assert tab.cache_daily == [] and tab.cache_daily_error

    def test_a_stop_mid_scan_ends_the_pass(self, tmp_path, monkeypatch):
        monitor, tab, log = make_monitor(tmp_path, monkeypatch, CHUNK_BYTES=37, CHUNK_PAUSE=0)
        log.write_bytes(b"".join(log_line(str(date.today()), 50) for _ in range(20)))
        stop = threading.Event()
        stop.set()
        monitor._scan(stop)
        assert monitor._offset < log.stat().st_size
