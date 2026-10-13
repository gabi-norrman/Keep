import os
import re
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from common.logging import info, trace
from common.Paths import LOG_DIR


class CacheHitLogMonitor:
    """The median prompt-cache hit percentage of a day's Claude requests, per day, from the server
    log, for the DashboardTab's per-day cache chart.

    The median, not the mean: a few misses a day are normal — the first request after a prompt
    change, an idle gap past the cache TTL, a toolkit load — and on a day of twenty restarts
    they drag a mean twenty points under what a typical request got. The median reads through
    them. The percentage is an integer, so a histogram of 101 buckets per day is the whole
    state and the median comes out exact.

    `log/floxy.out` is appended across runs and never rotated, so it holds the cache lines of
    every request the machine ever made — hundreds of megabytes. The scan runs once at startup
    and once a day after that, on a daemon thread put in the OS's background band so a voice
    turn never waits on it, and it is incremental: the day sums are kept and a later pass reads
    only the bytes appended since (a shrunk file is a new file and is read from the start).
    """

    LOG_FILE = LOG_DIR / "floxy.out"
    SCAN_INTERVAL = 24 * 3600.0
    MAX_DAYS = 120
    BUCKETS = 101               # one per integer percentage, 0 through 100
    CHUNK_BYTES = 4 * 1024 * 1024
    CHUNK_PAUSE = 0.005         # a breath between chunks so the GIL goes round while a run is live
    # `… Claude cache: 1200 read, 0 created (0 5m + 0 1h), 300 uncached (total 1500, 80% cached)`
    # — the line's timestamp wrapped in colour codes, so the date is found, not positioned.
    # Claude's lines only: Ollama logs a `% cached` of its own that the live chart leaves out too.
    NEEDLE = b"Claude cache: "
    LINE_RE = re.compile(rb"(\d{4}-\d{2}-\d{2}) \d\d:\d\d:\d\d.*?Claude cache: .*?, (\d+)% cached\)")

    def __init__(self, dashboard_tab, scan_interval: float = SCAN_INTERVAL):
        self._dashboard_tab = dashboard_tab
        self._scan_interval = scan_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._offset = 0
        self._tail = b""
        self._histograms: Dict[date, List[int]] = defaultdict(lambda: [0] * self.BUCKETS)    # day -> requests per pct

    def start(self):
        if self._thread and self._thread.is_alive() and not self._stop_event.is_set():
            return
        # A stopped loop still mid-scan keeps the event it was started with and ends on it;
        # this start gets a fresh one (see the other dashboard monitors).
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._monitor_loop, args=(self._stop_event,), daemon=True,
                                        name="CacheHitLogMonitor")
        self._thread.start()
        trace("CacheHitLogMonitor started")

    def stop(self):
        """Returns at once; the loop ends by itself at its next look at its event."""
        self._stop_event.set()

    def _monitor_loop(self, stop_event: threading.Event):
        self._lower_priority()
        while not stop_event.is_set():
            try:
                self._scan(stop_event)
            except Exception as e:
                trace(f"CacheHitLogMonitor error: {e}")
                self._dashboard_tab.cache_daily_error = str(e)
            stop_event.wait(self._scan_interval)

    @staticmethod
    def _lower_priority():
        """The calling thread into macOS's background QoS band (Python 3.12 exposes the darwin
        constants); elsewhere the thread stays where it is — the chunk pauses still apply."""
        thread_flag, background = getattr(os, "PRIO_DARWIN_THREAD", None), getattr(os, "PRIO_DARWIN_BG", None)
        if thread_flag is None or background is None:
            trace("CacheHitLogMonitor: no per-thread priority on this platform")
            return
        try:
            os.setpriority(thread_flag, 0, background)
        except OSError as e:
            trace(f"CacheHitLogMonitor: could not lower the thread priority: {e}")

    def _scan(self, stop_event: threading.Event):
        path = Path(self.LOG_FILE)
        if not path.exists():
            self._dashboard_tab.cache_daily = []
            self._dashboard_tab.cache_daily_error = f"Log not found: {path}"
            trace(f"CacheHitLogMonitor: {path} does not exist")
            return
        size = path.stat().st_size
        if size < self._offset:
            info(f"CacheHitLogMonitor: {path.name} shrank from {self._offset} to {size} bytes, rescanning it whole")
            self._offset, self._tail = 0, b""
            self._histograms.clear()
        if size == self._offset:
            trace("CacheHitLogMonitor: nothing appended to the log since the last scan")
            self._publish()
            return

        # The pass ends at the size it started with: the log grows while it runs — with this
        # monitor's own lines among the growth — and the rest is the next pass's.
        lines = 0
        with path.open("rb") as f:
            f.seek(self._offset)
            while not stop_event.is_set() and self._offset < size:
                chunk = f.read(min(self.CHUNK_BYTES, size - self._offset))
                if not chunk:
                    break
                self._offset += len(chunk)
                buffer = self._tail + chunk
                pieces = buffer.split(b"\n")
                self._tail = pieces.pop()
                lines += self._take(pieces)
                stop_event.wait(self.CHUNK_PAUSE)
        self._publish()
        info(f"CacheHitLogMonitor: read {lines} cache lines, {len(self._histograms)} days with requests, "
             f"log offset now {self._offset}")

    def _take(self, lines: List[bytes]) -> int:
        taken = 0
        for line in lines:
            if self.NEEDLE not in line:
                continue
            # A line that mentions the cache without being a cache line (a quote in a reply the
            # Telegram bot logged) is skipped in silence: a trace of it would land in this log
            # and be read back on every startup pass.
            match = self.LINE_RE.search(line)
            if not match:
                continue
            try:
                day = datetime.strptime(match.group(1).decode(), "%Y-%m-%d").date()
            except ValueError:
                continue
            self._histograms[day][min(int(match.group(2)), self.BUCKETS - 1)] += 1
            taken += 1
        return taken

    def _publish(self):
        self._dashboard_tab.cache_daily = self.daily_medians(self._histograms, date.today())
        self._dashboard_tab.cache_daily_error = ""

    @classmethod
    def daily_medians(cls, histograms: Dict[date, List[int]], today: date) -> List[Tuple[date, Optional[float]]]:
        """One entry per calendar day from the first day with requests (at most `MAX_DAYS` back)
        through `today`: the median hit percentage of the day's requests, or None for a day
        without one — a gap in the chart, not a 0 % that would read as a cache that missed all
        day."""
        days_with_requests = [d for d, histogram in histograms.items() if any(histogram)]
        if not days_with_requests:
            return []
        first = max(min(days_with_requests), today - timedelta(days=cls.MAX_DAYS - 1))
        series = []
        for i in range((today - first).days + 1):
            day = first + timedelta(days=i)
            histogram = histograms.get(day)
            series.append((day, cls.median(histogram) if histogram and any(histogram) else None))
        return series

    @staticmethod
    def median(histogram: List[int]) -> float:
        """The median of the values a histogram counts (index = value): the middle one, or the
        mean of the two middle ones when the count is even."""
        count = sum(histogram)
        lower_rank, upper_rank = (count - 1) // 2, count // 2
        seen, middle = 0, []
        for value, n in enumerate(histogram):
            seen += n
            while len(middle) < 2 and seen > (lower_rank, upper_rank)[len(middle)]:
                middle.append(value)
            if len(middle) == 2:
                break
        return (middle[0] + middle[1]) / 2
