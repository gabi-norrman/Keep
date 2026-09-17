import os
import time
import pytest
from unittest.mock import patch

from common.audio_log import cleanup_audio_folder


@pytest.mark.unit
class TestCleanupAudioFolder:
    def test_under_limit_no_files_deleted(self, tmp_path):
        (tmp_path / "a.wav").write_bytes(b"x" * 100)
        (tmp_path / "b.wav").write_bytes(b"x" * 100)

        cleanup_audio_folder(str(tmp_path), max_size_gb=1)

        assert len(list(tmp_path.iterdir())) == 2

    def test_over_limit_oldest_files_deleted_first(self, tmp_path):
        old_file = tmp_path / "old.wav"
        old_file.write_bytes(b"x" * 500)
        os.utime(old_file, (1000, 1000))

        mid_file = tmp_path / "mid.wav"
        mid_file.write_bytes(b"x" * 500)
        os.utime(mid_file, (2000, 2000))

        new_file = tmp_path / "new.wav"
        new_file.write_bytes(b"x" * 500)
        os.utime(new_file, (3000, 3000))

        # max_size_gb is very small so total (1500 bytes) exceeds it
        cleanup_audio_folder(str(tmp_path), max_size_gb=1000 / (1024 ** 3))

        remaining = {f.name for f in tmp_path.iterdir()}
        assert "new.wav" in remaining
        assert "old.wav" not in remaining

    def test_empty_folder_no_error(self, tmp_path):
        cleanup_audio_folder(str(tmp_path), max_size_gb=1)

    def test_oserror_on_delete_continues(self, tmp_path):
        file1 = tmp_path / "locked.wav"
        file1.write_bytes(b"x" * 500)
        os.utime(file1, (1000, 1000))

        file2 = tmp_path / "deletable.wav"
        file2.write_bytes(b"x" * 500)
        os.utime(file2, (2000, 2000))

        original_remove = os.remove

        def failing_remove(path):
            if "locked" in path:
                raise OSError("Permission denied")
            original_remove(path)

        with patch('os.remove', side_effect=failing_remove):
            cleanup_audio_folder(str(tmp_path), max_size_gb=100 / (1024 ** 3))

        # locked.wav should still exist (remove failed), deletable.wav should be deleted
        remaining = {f.name for f in tmp_path.iterdir()}
        assert "locked.wav" in remaining
        assert "deletable.wav" not in remaining


from common.StderrLogger import StderrLogger


@pytest.mark.unit
class TestStderrLogger:
    def test_rapid_writes_buffered_into_single_message(self):
        logger = StderrLogger()
        with patch('common.logging.info') as mock_info:
            logger.write("T")
            logger.write("r")
            logger.write("a")
            logger.write("c")
            logger.write("e")
            logger.flush()
            mock_info.assert_called_once_with("Trace")

    def test_flush_empty_buffer_does_not_log(self):
        logger = StderrLogger()
        with patch('common.logging.info') as mock_info:
            logger.flush()
            mock_info.assert_not_called()

    def test_whitespace_only_writes_do_not_log(self):
        logger = StderrLogger()
        with patch('common.logging.info') as mock_info:
            logger.write("   ")
            logger.write("\n")
            logger.flush()
            mock_info.assert_not_called()

    def test_timer_flushes_after_idle(self):
        logger = StderrLogger()
        with patch('common.logging.info') as mock_info:
            logger.write("delayed message")
            deadline = time.monotonic() + 2.0
            while not mock_info.called and time.monotonic() < deadline:
                time.sleep(0.01)
            mock_info.assert_called_once_with("delayed message")

    def test_flush_cancels_pending_timer(self):
        logger = StderrLogger()
        with patch('common.logging.info') as mock_info:
            logger.write("immediate")
            logger.flush()
            mock_info.assert_called_once_with("immediate")
            assert logger._timer is None

    def test_multiline_traceback_combined(self):
        logger = StderrLogger()
        with patch('common.logging.info') as mock_info:
            logger.write("Traceback (most recent call last):\n")
            logger.write("  File \"test.py\", line 1\n")
            logger.write("ValueError: bad value\n")
            logger.flush()
            mock_info.assert_called_once()
            logged = mock_info.call_args[0][0]
            assert "Traceback" in logged
            assert "ValueError" in logged
