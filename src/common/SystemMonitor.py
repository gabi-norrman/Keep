import sqlite3
import threading
import time
import os
import psutil
from typing import Optional
from .ConfigManager import config
from .logging import trace

PHOTOS_SQLITE_PATH = os.path.expanduser(
    "~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite"
)


class SystemMonitor:
    """Samples process/system CPU, memory, file descriptors and storage sizes into the TUI console.

    Runs on a daemon thread; the slow metrics (DB size, voice cache size, photo counts)
    are refreshed only every ~30s regardless of update_interval.
    """

    def __init__(self, tui_console=None, update_interval: float = 1.0):
        self.tui_console = tui_console
        self.update_interval = update_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process = psutil.Process(os.getpid())
        self._fd_dir = "/dev/fd"
        self._cpu_count = psutil.cpu_count()
        self._db_path = "floxy.db"
        self._voice_cache_dir = "cache/voice"
        self._voice_cache_max_gb = config.get('voice_cache_size_in_gb', 2)
        self._tick = 0
        # Slow metrics (DB/voice cache/photos) run every ~30s, independent of update_interval
        self._slow_metric_every = max(1, round(30.0 / update_interval))

        self._process.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="SystemMonitor")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _monitor_loop(self):
        while self._running:
            try:
                self._update_metrics()
            except Exception as e:
                print(f"SystemMonitor error: {e}")
            time.sleep(self.update_interval)

    def _update_metrics(self):
        if not self.tui_console:
            return

        vm = psutil.virtual_memory()
        self.tui_console.system_total_memory_gb = vm.total / (1024 ** 3)
        self.tui_console.system_used_memory_gb = vm.used / (1024 ** 3)
        self.tui_console.system_available_memory_gb = vm.available / (1024 ** 3)

        self.tui_console.process_memory_gb = self._process.memory_info().rss / (1024 ** 3)

        process_cpu_raw = self._process.cpu_percent(interval=None)
        self.tui_console.process_cpu_percent = process_cpu_raw / self._cpu_count if self._cpu_count > 0 else process_cpu_raw
        self.tui_console.system_cpu_percent = psutil.cpu_percent(interval=None)

        self.tui_console.fd_count = self._count_file_descriptors()

        # Slow metrics: DB size, voice cache, photos — only update every ~30 seconds
        self._tick += 1
        if self._tick % self._slow_metric_every == 1:
            self.tui_console.db_size_gb = self._get_database_size_gb()
            self.tui_console.voice_cache_size_gb = self._get_voice_cache_size_gb()
            self.tui_console.voice_cache_max_gb = self._voice_cache_max_gb
            photos_indexed, photos_total = self._get_photo_counts()
            self.tui_console.photos_indexed = photos_indexed
            self.tui_console.photos_total = photos_total

    def _count_file_descriptors(self) -> int:
        try:
            count = 0
            for entry in os.listdir(self._fd_dir):
                if entry not in (".", ".."):
                    count += 1
            return count
        except Exception:
            return 0

    def _get_database_size_gb(self) -> float:
        try:
            if os.path.exists(self._db_path):
                size_bytes = os.path.getsize(self._db_path)
                return size_bytes / (1024 ** 3)
            return 0.0
        except Exception:
            return 0.0

    def _get_photo_counts(self) -> tuple[int, int]:
        """(indexed, total) for the "Processed photos" bar: the Photos feed's own stored and
        total while it runs, so the bar and the Feeds panel agree; the index table over the
        library's stills when it does not."""
        try:
            from assistant.integration.FeedRunner import Feeds
            from .FloxyDatabase import PHOTO_HISTORY_SOURCE
            status = Feeds.get_instance().status(PHOTO_HISTORY_SOURCE)
            if status is not None and status.get("total") is not None:
                return status["stored"], status["total"]
        except Exception as e:
            trace(f"SystemMonitor: Photos feed status unavailable ({type(e).__name__}: {e}), counting the library")
        try:
            from .FloxyDatabase import FloxyDatabase
            indexed = FloxyDatabase.get_instance().get_photo_count()
        except Exception:
            indexed = 0
        try:
            if not os.path.exists(PHOTOS_SQLITE_PATH):
                return indexed, 0
            conn = sqlite3.connect(f"file:{PHOTOS_SQLITE_PATH}?mode=ro", uri=True)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM ZASSET WHERE ZKIND = 0 AND ZTRASHEDSTATE = 0 AND ZHIDDEN = 0")
                total = cursor.fetchone()[0]
                return indexed, total
            finally:
                conn.close()
        except Exception:
            return indexed, 0

    def _get_voice_cache_size_gb(self) -> float:
        try:
            if not os.path.exists(self._voice_cache_dir):
                return 0.0
            total_size = 0
            for entry in os.listdir(self._voice_cache_dir):
                file_path = os.path.join(self._voice_cache_dir, entry)
                if os.path.isfile(file_path):
                    total_size += os.path.getsize(file_path)
            return total_size / (1024 ** 3)
        except Exception:
            return 0.0
