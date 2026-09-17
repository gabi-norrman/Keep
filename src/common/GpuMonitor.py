import re
import subprocess
import threading
import time
from typing import Optional

from common.logging import trace


class GpuMonitor:
    """Samples Apple GPU utilization and memory from ioreg on a background thread for the GPU tab."""

    def __init__(self, gpu_tab, update_interval: float = 2.0):
        self._gpu_tab = gpu_tab
        self._update_interval = update_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._static_fetched = False
        self._last_history_sample = 0.0
        self._model = ""
        self._gpu_core_count = 0
        self._cpu_brand = ""
        self._p_cores = 0
        self._e_cores = 0
        self._total_memory_gb = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="GpuMonitor")
        self._thread.start()
        trace("GpuMonitor started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=0.1)

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                self._update_metrics()
            except Exception as e:
                trace(f"GpuMonitor error: {e}")
            self._stop_event.wait(self._update_interval)

    def _update_metrics(self):
        ioreg_data = self._parse_ioreg()
        if not ioreg_data:
            return

        if not self._static_fetched:
            self._fetch_static_data(ioreg_data)
            self._static_fetched = True

        tab = self._gpu_tab
        tab.device_util = ioreg_data.get("Device Utilization %", 0)
        tab.tiler_util = ioreg_data.get("Tiler Utilization %", 0)
        tab.renderer_util = ioreg_data.get("Renderer Utilization %", 0)
        tab.alloc_memory = ioreg_data.get("Alloc system memory", 0)
        tab.in_use_memory = ioreg_data.get("In use system memory", 0)
        tab.in_use_memory_driver = ioreg_data.get("In use system memory (driver)", 0)
        tab.pb_size = ioreg_data.get("Allocated PB Size", 0)
        tab.tiled_scene_bytes = ioreg_data.get("TiledSceneBytes", 0)
        tab.recovery_count = ioreg_data.get("recoveryCount", 0)

        tab.model = self._model
        tab.gpu_core_count = self._gpu_core_count
        tab.cpu_brand = self._cpu_brand
        tab.p_cores = self._p_cores
        tab.e_cores = self._e_cores
        tab.total_memory_gb = self._total_memory_gb

        # Bars update every cycle; throttle chart history to ~1s so the trend
        # charts keep their window regardless of how fast the bars refresh.
        now = time.monotonic()
        if now - self._last_history_sample >= 1.0:
            self._last_history_sample = now
            tab.device_history.append(tab.device_util)
            tab.tiler_history.append(tab.tiler_util)
            tab.renderer_history.append(tab.renderer_util)
            tab.memory_history.append(
                (tab.in_use_memory / tab.alloc_memory * 100.0) if tab.alloc_memory > 0 else 0
            )

    def _fetch_static_data(self, ioreg_data: dict):
        self._model = ioreg_data.get("_model", "")
        self._gpu_core_count = ioreg_data.get("_gpu_core_count", 0)

        try:
            self._cpu_brand = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3
            ).stdout.strip()
        except Exception:
            self._cpu_brand = ""

        try:
            self._p_cores = int(subprocess.run(
                ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                capture_output=True, text=True, timeout=3
            ).stdout.strip())
        except Exception:
            self._p_cores = 0

        try:
            self._e_cores = int(subprocess.run(
                ["sysctl", "-n", "hw.perflevel1.physicalcpu"],
                capture_output=True, text=True, timeout=3
            ).stdout.strip())
        except Exception:
            self._e_cores = 0

        try:
            memsize_bytes = int(subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3
            ).stdout.strip())
            self._total_memory_gb = round(memsize_bytes / (1024 ** 3))
        except Exception:
            self._total_memory_gb = 0

        trace(f"GpuMonitor: {self._model}, {self._gpu_core_count} GPU cores, "
             f"{self._p_cores}P+{self._e_cores}E CPU cores, {self._total_memory_gb}GB RAM")

    def _parse_ioreg(self) -> Optional[dict]:
        try:
            result = subprocess.run(
                ["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None

            output = result.stdout
            data = {}

            stats_match = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]+)\}', output)
            if stats_match:
                pairs = re.findall(r'"([^"]+)"\s*=\s*(\d+)', stats_match.group(1))
                for key, value in pairs:
                    data[key] = int(value)

            model_match = re.search(r'"model"\s*=\s*"([^"]+)"', output)
            if model_match:
                data["_model"] = model_match.group(1)

            cores_match = re.search(r'"gpu-core-count"\s*=\s*(\d+)', output)
            if cores_match:
                data["_gpu_core_count"] = int(cores_match.group(1))

            return data

        except Exception as e:
            trace(f"GpuMonitor: ioreg parse failed: {e}")
            return None
