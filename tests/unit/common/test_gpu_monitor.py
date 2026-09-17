from unittest.mock import Mock, patch, MagicMock
import pytest

from common.GpuMonitor import GpuMonitor

SAMPLE_IOREG_OUTPUT = """\
+-o AGXAcceleratorG13x_B0  <class AGXAcceleratorG13x_B0, id 0x100000a4f, registered, matched, active, busy 0 (2 ms), retain 10>
    {
      "gpu-core-count" = 30
      "model" = "Apple M2 Max"
      "IOClass" = "AGXAcceleratorG13x_B0"
      "PerformanceStatistics" = {"Device Utilization %"=42,"Tiler Utilization %"=18,"Renderer Utilization %"=37,"Alloc system memory"=2147483648,"In use system memory"=536870912,"In use system memory (driver)"=134217728,"Allocated PB Size"=4194304,"TiledSceneBytes"=1048576,"recoveryCount"=0}
    }
"""

SAMPLE_IOREG_MINIMAL = """\
+-o AGXAccelerator  <class AGXAccelerator>
    {
      "PerformanceStatistics" = {"Device Utilization %"=95,"Renderer Utilization %"=80}
    }
"""

SAMPLE_IOREG_NO_STATS = """\
+-o AGXAccelerator  <class AGXAccelerator>
    {
      "IOClass" = "AGXAccelerator"
    }
"""


@pytest.mark.unit
class TestGpuMonitorParseIoreg:

    def _make_monitor(self):
        tab = Mock()
        return GpuMonitor(tab)

    @patch('common.GpuMonitor.subprocess.run')
    def test_parses_full_ioreg_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=SAMPLE_IOREG_OUTPUT)
        monitor = self._make_monitor()
        data = monitor._parse_ioreg()

        assert data["Device Utilization %"] == 42
        assert data["Tiler Utilization %"] == 18
        assert data["Renderer Utilization %"] == 37
        assert data["Alloc system memory"] == 2147483648
        assert data["In use system memory"] == 536870912
        assert data["In use system memory (driver)"] == 134217728
        assert data["Allocated PB Size"] == 4194304
        assert data["TiledSceneBytes"] == 1048576
        assert data["recoveryCount"] == 0

    @patch('common.GpuMonitor.subprocess.run')
    def test_parses_model_and_gpu_cores(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=SAMPLE_IOREG_OUTPUT)
        monitor = self._make_monitor()
        data = monitor._parse_ioreg()

        assert data["_model"] == "Apple M2 Max"
        assert data["_gpu_core_count"] == 30

    @patch('common.GpuMonitor.subprocess.run')
    def test_minimal_output_missing_keys(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=SAMPLE_IOREG_MINIMAL)
        monitor = self._make_monitor()
        data = monitor._parse_ioreg()

        assert data["Device Utilization %"] == 95
        assert data["Renderer Utilization %"] == 80
        assert "_model" not in data
        assert "_gpu_core_count" not in data

    @patch('common.GpuMonitor.subprocess.run')
    def test_no_performance_stats(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=SAMPLE_IOREG_NO_STATS)
        monitor = self._make_monitor()
        data = monitor._parse_ioreg()

        assert data == {}

    @patch('common.GpuMonitor.subprocess.run')
    def test_nonzero_returncode_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        monitor = self._make_monitor()
        assert monitor._parse_ioreg() is None

    @patch('common.GpuMonitor.subprocess.run')
    def test_subprocess_exception_returns_none(self, mock_run):
        mock_run.side_effect = Exception("timeout")
        monitor = self._make_monitor()
        assert monitor._parse_ioreg() is None


@pytest.mark.unit
class TestGpuMonitorHistoryThrottle:
    """The bars refresh at update_interval (~0.33s) but trend-chart history must stay at
    ~1s/sample so the ~120s window doesn't shrink to ~40s when bars speed up (676e262d)."""

    def _make_monitor(self):
        tab = Mock()
        tab.device_history = []
        tab.tiler_history = []
        tab.renderer_history = []
        tab.memory_history = []
        monitor = GpuMonitor(tab)
        monitor._static_fetched = True  # skip the sysctl shell-outs in _fetch_static_data
        return monitor

    def _ioreg_data(self):
        return {
            "Device Utilization %": 10,
            "Tiler Utilization %": 20,
            "Renderer Utilization %": 30,
            "Alloc system memory": 100,
            "In use system memory": 50,
            "In use system memory (driver)": 10,
            "Allocated PB Size": 1,
            "TiledSceneBytes": 1,
            "recoveryCount": 0,
        }

    def test_first_call_appends_history(self):
        monitor = self._make_monitor()
        with patch.object(monitor, '_parse_ioreg', return_value=self._ioreg_data()), \
             patch('common.GpuMonitor.time.monotonic', return_value=100.0):
            monitor._update_metrics()
        assert monitor._gpu_tab.device_history == [10]
        assert monitor._gpu_tab.memory_history == [50.0]  # 50/100 * 100

    def test_rapid_second_call_within_1s_does_not_append(self):
        monitor = self._make_monitor()
        with patch.object(monitor, '_parse_ioreg', return_value=self._ioreg_data()), \
             patch('common.GpuMonitor.time.monotonic', side_effect=[100.0, 100.33, 100.66]):
            monitor._update_metrics()  # t=100.0 → append
            monitor._update_metrics()  # t=100.33 → skip
            monitor._update_metrics()  # t=100.66 → skip
        assert monitor._gpu_tab.device_history == [10]
        assert monitor._gpu_tab.tiler_history == [20]
        assert monitor._gpu_tab.renderer_history == [30]

    def test_call_after_1s_appends_again(self):
        monitor = self._make_monitor()
        with patch.object(monitor, '_parse_ioreg', return_value=self._ioreg_data()), \
             patch('common.GpuMonitor.time.monotonic', side_effect=[100.0, 100.5, 101.5]):
            monitor._update_metrics()  # t=100.0 → append
            monitor._update_metrics()  # t=100.5 → skip
            monitor._update_metrics()  # t=101.5 → append (1.5s gap)
        assert monitor._gpu_tab.device_history == [10, 10]

    def test_zero_alloc_memory_records_zero_percent(self):
        monitor = self._make_monitor()
        data = self._ioreg_data()
        data["Alloc system memory"] = 0
        data["In use system memory"] = 0
        with patch.object(monitor, '_parse_ioreg', return_value=data), \
             patch('common.GpuMonitor.time.monotonic', return_value=100.0):
            monitor._update_metrics()
        assert monitor._gpu_tab.memory_history == [0]
