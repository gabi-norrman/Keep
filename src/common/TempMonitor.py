import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from common.logging import info, trace


class TempMonitor:
    """Samples Apple Silicon CPU/GPU temperature via the `macmon` CLI into the DashboardTab.

    Runs on a daemon thread; needs no sudo, and reports a missing binary via INSTALL_HINT.
    """

    SAMPLE_TIMEOUT = 15
    INSTALL_HINT = ("install with `brew install macmon`, or download the binary from "
                    "https://github.com/vladkens/macmon/releases and put it in ~/bin")

    @classmethod
    def find_macmon(cls) -> Optional[str]:
        path = shutil.which("macmon")
        if not path:
            home_bin = Path.home() / "bin" / "macmon"
            if home_bin.exists():
                path = str(home_bin)
        return path

    @classmethod
    def check_installed(cls) -> bool:
        """Startup check: log loudly if macmon is missing so temp monitoring isn't silently dead."""
        path = cls.find_macmon()
        if path:
            trace(f"TempMonitor: macmon found at {path}")
            return True
        trace(f"TempMonitor: macmon is not installed — temperature monitoring disabled. "
              f"To fix: {cls.INSTALL_HINT}")
        return False

    def __init__(self, dashboard_tab, update_interval: float = 30.0):
        self._dashboard_tab = dashboard_tab
        self._update_interval = update_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._macmon_path: Optional[str] = None
        self._reported_missing = False

    def start(self):
        if self._thread and self._thread.is_alive() and not self._stop_event.is_set():
            return
        # A stopped loop still mid-sample keeps the event it was started with and ends on it;
        # this start gets a fresh one. With one event shared, coming back to the tab before
        # the old loop had ended started nothing and left the monitor dead while the tab showed.
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._monitor_loop, args=(self._stop_event,), daemon=True,
                                        name="TempMonitor")
        self._thread.start()
        trace("TempMonitor started")

    def stop(self):
        """Returns at once — a tab switch calls it on the TUI's input thread, and a join here
        held every keypress back while the loop was mid-sample. The loop ends by itself at
        its next look at its event."""
        self._stop_event.set()

    def _monitor_loop(self, stop_event: threading.Event):
        while not stop_event.is_set():
            try:
                self._sample()
            except Exception as e:
                trace(f"TempMonitor error: {e}")
                self._dashboard_tab.temp_error = str(e)
            stop_event.wait(self._update_interval)

    def _find_macmon(self) -> Optional[str]:
        if self._macmon_path and Path(self._macmon_path).exists():
            return self._macmon_path
        path = self.find_macmon()
        if path:
            self._macmon_path = path
            trace(f"TempMonitor: using macmon at {path}")
        return path

    def _sample(self):
        macmon = self._find_macmon()
        if not macmon:
            if not self._reported_missing:
                self._reported_missing = True
                info("TempMonitor: macmon not found — temp chart disabled")
            self._dashboard_tab.temp_error = "macmon not found (brew install macmon)"
            return

        result = subprocess.run(
            [macmon, "pipe", "-s", "1", "-i", "1000"],
            capture_output=True, text=True, timeout=self.SAMPLE_TIMEOUT
        )
        if result.returncode != 0:
            self._dashboard_tab.temp_error = f"macmon failed (rc={result.returncode})"
            trace(f"TempMonitor: macmon rc={result.returncode}: {result.stderr.strip()[:200]}")
            return

        output = result.stdout.strip()
        if not output:
            self._dashboard_tab.temp_error = "macmon returned no data"
            trace("TempMonitor: empty macmon output")
            return

        metrics = json.loads(output.splitlines()[-1])
        temp = metrics.get("temp", {})
        cpu_temp = float(temp.get("cpu_temp_avg", 0.0))
        gpu_temp = float(temp.get("gpu_temp_avg", 0.0))

        tab = self._dashboard_tab
        tab.cpu_temp = cpu_temp
        tab.gpu_temp = gpu_temp
        tab.temp_error = ""
        tab.temp_history.append(cpu_temp)
