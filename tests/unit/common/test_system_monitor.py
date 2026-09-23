import pytest
from unittest.mock import Mock, patch
import threading


@pytest.fixture(autouse=True)
def no_real_photos_library(tmp_path):
    """The first `_update_metrics` tick counts the Photos library: without this every test that
    ticks once opens the developer's real Photos.sqlite (and waits most of a second for it)."""
    with patch('common.SystemMonitor.PHOTOS_SQLITE_PATH', str(tmp_path / "no-Photos.sqlite")):
        yield


@pytest.mark.unit
class TestSystemMonitorInit:
    """Tests for SystemMonitor initialization"""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 0
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_init_reads_voice_cache_config(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        mock_config.get.return_value = 5
        monitor = SystemMonitor()
        assert monitor._voice_cache_max_gb == 5
        mock_config.get.assert_called_with('voice_cache_size_in_gb', 2)

@pytest.mark.unit
class TestSystemMonitorCountFileDescriptors:
    """Tests for _count_file_descriptors()"""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 0
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_counts_file_descriptors(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.listdir', return_value=['0', '1', '2', '3', '4']):
            count = monitor._count_file_descriptors()
        assert count == 5

    def test_excludes_dot_entries(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.listdir', return_value=['.', '..', '0', '1', '2']):
            count = monitor._count_file_descriptors()
        assert count == 3

    def test_returns_zero_on_exception(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.listdir', side_effect=OSError("Permission denied")):
            count = monitor._count_file_descriptors()
        assert count == 0

@pytest.mark.unit
class TestSystemMonitorDatabaseSize:
    """Tests for _get_database_size_gb()"""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 0
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_returns_size_in_gb(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', return_value=1024 ** 3):
            size = monitor._get_database_size_gb()
        assert size == 1.0

    def test_returns_zero_if_not_exists(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.path.exists', return_value=False):
            size = monitor._get_database_size_gb()
        assert size == 0.0

    def test_returns_zero_on_exception(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.path.exists', side_effect=OSError("Error")):
            size = monitor._get_database_size_gb()
        assert size == 0.0

@pytest.mark.unit
class TestSystemMonitorVoiceCacheSize:
    """Tests for _get_voice_cache_size_gb()"""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 0
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_returns_zero_if_dir_not_exists(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.path.exists', return_value=False):
            size = monitor._get_voice_cache_size_gb()
        assert size == 0.0

    def test_sums_file_sizes(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()

        def exists_side_effect(path):
            return True

        def isfile_side_effect(path):
            return not path.endswith('subdir')

        with patch('os.path.exists', side_effect=exists_side_effect), \
             patch('os.listdir', return_value=['file1.mp3', 'file2.mp3', 'subdir']), \
             patch('os.path.isfile', side_effect=isfile_side_effect), \
             patch('os.path.getsize', return_value=512 * 1024 * 1024), \
             patch('os.path.join', side_effect=lambda a, b: f"{a}/{b}"):
            size = monitor._get_voice_cache_size_gb()
        assert abs(size - 1.0) < 0.001

    def test_returns_zero_on_exception(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', side_effect=OSError("Permission denied")):
            size = monitor._get_voice_cache_size_gb()
        assert size == 0.0

@pytest.mark.unit
class TestSystemMonitorUpdateMetrics:
    """Tests for _update_metrics()"""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 50.0
            mock_process.memory_info.return_value = Mock(rss=2 * 1024 ** 3)
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 25.0
            mock.cpu_count.return_value = 8
            mock_vm = Mock()
            mock_vm.total = 16 * 1024 ** 3
            mock_vm.used = 8 * 1024 ** 3
            mock_vm.percent = 50.0
            mock.virtual_memory.return_value = mock_vm
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_sets_memory_metrics(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        console = Mock()
        monitor = SystemMonitor(tui_console=console)

        with patch.object(monitor, '_count_file_descriptors', return_value=100), \
             patch.object(monitor, '_get_database_size_gb', return_value=0.1), \
             patch.object(monitor, '_get_voice_cache_size_gb', return_value=0.5):
            monitor._update_metrics()

        assert console.system_total_memory_gb == 16.0
        assert console.system_used_memory_gb == 8.0

    def test_sets_process_metrics(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        console = Mock()
        monitor = SystemMonitor(tui_console=console)

        with patch.object(monitor, '_count_file_descriptors', return_value=100), \
             patch.object(monitor, '_get_database_size_gb', return_value=0.1), \
             patch.object(monitor, '_get_voice_cache_size_gb', return_value=0.5):
            monitor._update_metrics()

        assert console.process_memory_gb == 2.0
        assert console.process_cpu_percent == 50.0 / 8

@pytest.mark.unit
class TestSystemMonitorSlowMetricCadence:
    """The slow metrics (DB size, voice cache, Photos SQLite COUNT) are expensive and must
    stay at ~30s, even when update_interval is shortened to make the bars feel snappier
    (676e262d). The tick divisor is derived from update_interval so the wall-clock cadence
    is preserved."""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 0
            mock_process.memory_info.return_value = Mock(rss=0)
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            mock_vm = Mock()
            mock_vm.total = 0
            mock_vm.used = 0
            mock_vm.percent = 0
            mock.virtual_memory.return_value = mock_vm
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_divisor_at_1s_interval(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor(update_interval=1.0)
        assert monitor._slow_metric_every == 30

    def test_divisor_at_033s_interval_keeps_30s_cadence(self, mock_psutil, mock_config):
        """0.33s bars × 91 ticks ≈ 30s — the whole point of the fix."""
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor(update_interval=0.33)
        assert monitor._slow_metric_every == 91

    def test_divisor_clamped_to_at_least_1(self, mock_psutil, mock_config):
        """If update_interval ≥ 30s, slow metrics must still fire every tick (not 0)."""
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor(update_interval=60.0)
        assert monitor._slow_metric_every == 1

    def test_slow_metrics_fire_on_first_tick_then_skip(self, mock_psutil, mock_config):
        """Slow metrics fire when _tick % divisor == 1: first call after construction
        sets _tick=1 (fires), the next divisor-1 calls skip, then the cycle repeats."""
        from common.SystemMonitor import SystemMonitor
        console = Mock()
        monitor = SystemMonitor(tui_console=console, update_interval=1.0)  # divisor=30

        with patch.object(monitor, '_count_file_descriptors', return_value=0), \
             patch.object(monitor, '_get_database_size_gb', return_value=0.42) as mock_db, \
             patch.object(monitor, '_get_voice_cache_size_gb', return_value=0.1), \
             patch.object(monitor, '_get_photo_counts', return_value=(1, 2)):
            monitor._update_metrics()  # tick 1 → fires
            assert mock_db.call_count == 1
            assert console.db_size_gb == 0.42
            monitor._update_metrics()  # tick 2 → skip
            monitor._update_metrics()  # tick 3 → skip
            assert mock_db.call_count == 1

    def test_slow_metrics_refire_after_divisor_ticks(self, mock_psutil, mock_config):
        """At update_interval=5s the divisor is 6, so slow metrics should fire on
        ticks 1, 7, 13, … — covers the modulo wrap-around, not just the first hit."""
        from common.SystemMonitor import SystemMonitor
        console = Mock()
        monitor = SystemMonitor(tui_console=console, update_interval=5.0)  # divisor=6

        with patch.object(monitor, '_count_file_descriptors', return_value=0), \
             patch.object(monitor, '_get_database_size_gb', return_value=0.0) as mock_db, \
             patch.object(monitor, '_get_voice_cache_size_gb', return_value=0.0), \
             patch.object(monitor, '_get_photo_counts', return_value=(0, 0)):
            for _ in range(7):
                monitor._update_metrics()
        # Ticks 1..7 with divisor 6 → fires on tick 1 and tick 7 → 2 calls.
        assert mock_db.call_count == 2


@pytest.mark.unit
class TestSystemMonitorStartStop:
    """Tests for start() and stop() thread lifecycle"""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock_process = Mock()
            mock_process.cpu_percent.return_value = 0
            mock.Process.return_value = mock_process
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_start_creates_thread(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch.object(threading.Thread, 'start'):
            monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.name == "SystemMonitor"
        assert monitor._thread.daemon is True

    def test_start_twice_is_noop(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        with patch.object(threading.Thread, 'start') as mock_start:
            monitor.start()
            first_thread = monitor._thread
            monitor.start()
        assert monitor._thread is first_thread
        mock_start.assert_called_once()

    def test_stop_clears_running_flag(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        mock_thread = Mock()
        monitor._running = True
        monitor._thread = mock_thread
        monitor.stop()
        assert monitor._running is False

    def test_stop_joins_thread(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        mock_thread = Mock()
        monitor._running = True
        monitor._thread = mock_thread
        monitor.stop()
        mock_thread.join.assert_called_once_with(timeout=2.0)

    def test_stop_without_thread_is_safe(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        monitor = SystemMonitor()
        monitor._running = True
        monitor._thread = None
        monitor.stop()  # Should not raise
        assert monitor._running is False


@pytest.mark.unit
class TestSystemMonitorPhotoCounts:
    """The "Processed photos" bar takes the Photos feed's stored / total while it runs, so it
    agrees with the Feeds panel; without the feed it counts the index table and the library."""

    @pytest.fixture
    def mock_psutil(self):
        with patch('common.SystemMonitor.psutil') as mock:
            mock.Process.return_value.cpu_percent.return_value = 0
            mock.cpu_percent.return_value = 0
            mock.cpu_count.return_value = 8
            yield mock

    @pytest.fixture
    def mock_config(self):
        with patch('common.SystemMonitor.config') as mock:
            mock.get.return_value = 2
            yield mock

    def test_the_running_photos_feed_supplies_both_numbers(self, mock_psutil, mock_config):
        from common.SystemMonitor import SystemMonitor
        from assistant.integration.FeedRunner import Feeds
        status = {"state": "catching_up", "stored": 87054, "remaining": 200, "total": 87254}
        with patch.object(Feeds, 'get_instance') as feeds:
            feeds.return_value.status.return_value = status
            assert SystemMonitor(tui_console=Mock())._get_photo_counts() == (87054, 87254)
        feeds.return_value.status.assert_called_once_with("iCloud Photos")

    def test_without_the_feed_the_index_table_is_counted_against_the_library(self, mock_psutil, mock_config, tmp_path):
        from common.SystemMonitor import SystemMonitor
        from assistant.integration.FeedRunner import Feeds
        library = tmp_path / "Photos.sqlite"
        import sqlite3
        with sqlite3.connect(library) as conn:
            conn.execute("CREATE TABLE ZASSET (ZKIND INTEGER, ZTRASHEDSTATE INTEGER, ZHIDDEN INTEGER)")
            conn.executemany("INSERT INTO ZASSET VALUES (?, ?, ?)", [(0, 0, 0), (0, 0, 0), (1, 0, 0), (0, 1, 0)])
        with patch.object(Feeds, 'get_instance') as feeds, \
             patch('common.SystemMonitor.PHOTOS_SQLITE_PATH', str(library)), \
             patch('common.FloxyDatabase.FloxyDatabase.get_instance') as db:
            feeds.return_value.status.return_value = None
            db.return_value.get_photo_count.return_value = 1
            assert SystemMonitor(tui_console=Mock())._get_photo_counts() == (1, 2)
