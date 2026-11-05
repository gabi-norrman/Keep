"""CacheHitLogMonitor: the per-day prompt-cache median the dashboard charts, scanned from
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


def histogram(*pcts: int) -> list:
    counts = [0] * CacheHitLogMonitor.BUCKETS
    for pct in pcts:
        counts[pct] += 1
    return counts


def make_monitor(tmp_path, monkeypatch, **overrides):
    tab = Mock()
    tab.cache_daily, tab.cache_daily_error = [], ""
    log = tmp_path / "floxy.out"
    monkeypatch.setattr(CacheHitLogMonitor, "LOG_FILE", log)
    for name, value in overrides.items():
        monkeypatch.setattr(CacheHitLogMonitor, name, value)
    return CacheHitLogMonitor(tab, scan_interval=0), tab, log


def monitor_take(tmp_path, monkeypatch, lines) -> dict:
    monitor, _, _ = make_monitor(tmp_path, monkeypatch)
    monitor._take(lines)
    return dict(monitor._histograms)


def scan(monitor):
    monitor._scan(threading.Event())


@pytest.mark.unit
class TestTheLogLine:
    def test_the_coloured_line_yields_its_day_and_percentage__contract_with_floxy_out(self, tmp_path, monkeypatch):
        monitor, _, _ = make_monitor(tmp_path, monkeypatch)
        assert monitor._take([log_line("2026-03-05", 80), *OTHER_LINES.split(b"\n")]) == 1
        assert dict(monitor._histograms) == {date(2026, 3, 5): histogram(80)}

    def test_a_local_backends_line_counts_like_claudes(self, tmp_path, monkeypatch):
        """ds4 and Ollama write their hit through AbstractLLM.record_cache_hit; the scan reads
        it beside Claude's own line and leaves out ds4's other `cache:` traces."""
        from assistant.llm.AbstractLLM import AbstractLLM
        llm = Mock(spec=AbstractLLM)
        llm.name.return_value, llm.model = "DS4", "deepseek-v4"
        written = []
        monkeypatch.setattr("assistant.llm.AbstractLLM.trace", written.append)
        monkeypatch.setattr("common.TTFTMonitor.TTFTMonitor.get_instance", staticmethod(Mock))
        AbstractLLM.record_cache_hit(llm, 900, 1000)
        stamp = "I│2026-03-05 09:40:17.000 │ main │ "
        lines = [f"{stamp}{written[0]}".encode(), f"{stamp}DS4 cache: the usage says 9/10 cached".encode()]
        assert monitor_take(tmp_path, monkeypatch, lines) == {date(2026, 3, 5): histogram(90)}

    def test_a_cache_line_of_another_shape_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        monitor, _, _ = make_monitor(tmp_path, monkeypatch)
        assert monitor._take([b"Claude cache: 5 read, 5 uncached (total 10, 50% cached)  <- no timestamp"]) == 0
        assert not monitor._histograms


@pytest.mark.unit
class TestDailyMedians:
    def test_a_day_is_the_median_of_its_requests_and_a_day_without_one_is_a_gap(self):
        histograms = {date(2026, 3, 5): histogram(70, 80, 95), date(2026, 3, 7): histogram(40)}
        assert CacheHitLogMonitor.daily_medians(histograms, TODAY) == [
            (date(2026, 3, 5), 80.0), (date(2026, 3, 6), None), (date(2026, 3, 7), 40.0)]

    def test_a_day_of_restarts_reads_as_what_a_typical_request_got(self):
        """Twenty restarts' worth of misses among a hundred hits: the mean would read 80, the
        chart should read the hit."""
        day = histogram(*([0] * 20 + [100] * 100))
        assert CacheHitLogMonitor.daily_medians({TODAY: day}, TODAY) == [(TODAY, 100.0)]

    def test_an_even_count_takes_the_mean_of_the_two_middle_requests(self):
        assert CacheHitLogMonitor.median(histogram(10, 20, 80, 90)) == 50.0
        assert CacheHitLogMonitor.median(histogram(0, 100)) == 50.0
        assert CacheHitLogMonitor.median(histogram(37)) == 37.0

    def test_the_series_runs_through_today_even_when_today_has_no_request_yet(self):
        series = CacheHitLogMonitor.daily_medians({date(2026, 3, 5): histogram(90)}, TODAY)
        assert series[-1] == (TODAY, None) and len(series) == 3

    def test_the_series_reaches_no_further_back_than_max_days(self):
        ancient = TODAY - timedelta(days=CacheHitLogMonitor.MAX_DAYS + 30)
        series = CacheHitLogMonitor.daily_medians({ancient: histogram(50), TODAY: histogram(10)}, TODAY)
        assert len(series) == CacheHitLogMonitor.MAX_DAYS
        assert series[-1] == (TODAY, 10.0)

    def test_no_requests_is_an_empty_series(self):
        assert CacheHitLogMonitor.daily_medians({}, TODAY) == []
        assert CacheHitLogMonitor.daily_medians({TODAY: histogram()}, TODAY) == []


@pytest.mark.unit
class TestScanning:
    def test_the_first_scan_takes_in_every_day_in_the_log(self, tmp_path, monkeypatch):
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
        assert tab.cache_daily == [(today, 50.0)], "a line counted twice would pull the median off"
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
