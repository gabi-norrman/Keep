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
    """Averages the prompt-cache hit percentage of every Claude request per day from the server
    log, for the DashboardTab's per-day cache chart.

    `log/floxy.out` is appended across runs and never rotated, so it holds the cache lines of
    every request the machine ever made — hundreds of megabytes. The scan runs once at startup
    and once a day after that, on a daemon thread put in the OS's background band so a voice
    turn never waits on it, and it is incremental: the day sums are kept and a later pass reads
    only the bytes appended since (a shrunk file is a new file and is read from the start).
    """

    LOG_FILE = LOG_DIR / "floxy.out"
    SCAN_INTERVAL = 24 * 3600.0
    MAX_DAYS = 120
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
        self._sums: Dict[date, List[int]] = defaultdict(lambda: [0, 0])    # day -> [sum of pct, count]

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
            self._sums.clear()
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
        info(f"CacheHitLogMonitor: read {lines} cache lines, {len(self._sums)} days with requests, "
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
            entry = self._sums[day]
            entry[0] += int(match.group(2))
            entry[1] += 1
            taken += 1
        return taken

    def _publish(self):
        self._dashboard_tab.cache_daily = self.daily_averages(self._sums, date.today())
        self._dashboard_tab.cache_daily_error = ""

    @classmethod
    def daily_averages(cls, sums: Dict[date, List[int]], today: date) -> List[Tuple[date, Optional[float]]]:
        """One entry per calendar day from the first day with requests (at most `MAX_DAYS` back)
        through `today`: the day's mean hit percentage, or None for a day without a request —
        a gap in the chart, not a 0 % that would read as a cache that missed all day."""
        days_with_requests = [d for d, (_, count) in sums.items() if count]
        if not days_with_requests:
            return []
        first = max(min(days_with_requests), today - timedelta(days=cls.MAX_DAYS - 1))
        series = []
        for i in range((today - first).days + 1):
            day = first + timedelta(days=i)
            total, count = sums.get(day, (0, 0))
            series.append((day, total / count if count else None))
        return series
