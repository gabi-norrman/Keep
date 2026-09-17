import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from common.logging import trace


class MemoryLogMonitor:
    """Scans log/memory snapshots on a background thread to chart memory size per day in the DashboardTab."""

    LOG_DIR = Path("log/memory")
    FILENAME_RE = re.compile(r"^(\d{8})_\d{6}_[0-9a-f]+_memory\.md$")
    NEW_SIZE_RE = re.compile(r"^- New size: (\d+) chars\s*$", re.MULTILINE)
    HEADER_READ_BYTES = 2048

    def __init__(self, dashboard_tab, fetch_interval: float = 300.0):
        self._dashboard_tab = dashboard_tab
        self._fetch_interval = fetch_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="MemoryLogMonitor")
        self._thread.start()
        trace("MemoryLogMonitor started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=0.1)

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                self._scan()
            except Exception as e:
                trace(f"MemoryLogMonitor error: {e}")
                self._dashboard_tab.memory_log_error = str(e)
            self._stop_event.wait(self._fetch_interval)

    def _scan(self):
        if not self.LOG_DIR.exists():
            self._dashboard_tab.memory_log_sizes = []
            self._dashboard_tab.memory_log_error = f"Directory not found: {self.LOG_DIR}"
            trace(f"MemoryLogMonitor: {self.LOG_DIR} does not exist")
            return

        # Per-day: keep the largest reading (handles future days with multiple snapshots)
        per_day: dict = {}
        skipped = 0
        for entry in self.LOG_DIR.iterdir():
            if not entry.is_file():
                continue
            match = self.FILENAME_RE.match(entry.name)
            if not match:
                skipped += 1
                trace(f"MemoryLogMonitor: skipping malformed name {entry.name}")
                continue
            try:
                day = datetime.strptime(match.group(1), "%Y%m%d").date()
            except ValueError:
                skipped += 1
                trace(f"MemoryLogMonitor: skipping unparseable date in {entry.name}")
                continue
            try:
                with entry.open("r", encoding="utf-8", errors="replace") as f:
                    header = f.read(self.HEADER_READ_BYTES)
            except OSError as e:
                skipped += 1
                trace(f"MemoryLogMonitor: cannot read {entry.name}: {e}")
                continue
            size_match = self.NEW_SIZE_RE.search(header)
            if not size_match:
                skipped += 1
                trace(f"MemoryLogMonitor: no 'New size' header in {entry.name}")
                continue
            size_kb = int(size_match.group(1)) / 1024.0
            existing = per_day.get(day)
            if existing is None or size_kb > existing:
                per_day[day] = size_kb

        sizes = sorted(per_day.items(), key=lambda kv: kv[0])
        self._dashboard_tab.memory_log_sizes = sizes
        self._dashboard_tab.memory_log_error = ""