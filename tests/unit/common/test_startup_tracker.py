import json
import subprocess
from unittest.mock import patch

import pytest

from common import StartupTracker as startup_tracker_module
from common.StartupTracker import StartupTracker


@pytest.fixture
def temp_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "cache" / "last_startup.json"
    monkeypatch.setattr(startup_tracker_module, "STATE_FILE", state_file)
    return state_file


@pytest.mark.unit
class TestReadPreviousStartup:
    def test_returns_none_when_missing(self, temp_state_file):
        assert StartupTracker.read_previous_startup() is None

    def test_returns_none_when_corrupt(self, temp_state_file):
        temp_state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_state_file.write_text("{not valid json")
        assert StartupTracker.read_previous_startup() is None

    def test_returns_none_when_field_missing(self, temp_state_file):
        temp_state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_state_file.write_text(json.dumps({"other": 1}))
        assert StartupTracker.read_previous_startup() is None

    def test_round_trip(self, temp_state_file):
        StartupTracker.write_current_startup(1700000000.0)
        assert temp_state_file.exists()
        assert StartupTracker.read_previous_startup() == 1700000000.0


@pytest.mark.unit
class TestWriteCurrentStartup:
    def test_creates_parent_dir(self, temp_state_file):
        assert not temp_state_file.parent.exists()
        StartupTracker.write_current_startup(123.0)
        assert temp_state_file.exists()
        data = json.loads(temp_state_file.read_text())
        assert data == {"started_at": 123.0}

    def test_overwrites_existing(self, temp_state_file):
        StartupTracker.write_current_startup(1.0)
        StartupTracker.write_current_startup(2.0)
        assert StartupTracker.read_previous_startup() == 2.0


@pytest.mark.unit
class TestBuildHistoryMessage:
    def test_no_previous_timestamp(self):
        msg = StartupTracker.build_history_message([], None)
        assert msg == "Server started up successfully."

    def test_no_commits(self):
        msg = StartupTracker.build_history_message([], 1700000000.0)
        assert msg == "Server started up successfully. No changes since last start."

    def test_with_commits(self):
        commits = ["abc1234 first commit", "def5678 second commit"]
        msg = StartupTracker.build_history_message(commits, 1700000000.0)
        assert msg.startswith("Server started up successfully. Change log since last start:\n")
        assert "abc1234 first commit" in msg
        assert "def5678 second commit" in msg


@pytest.mark.unit
class TestRecordShutdown:
    def test_writes_history_entry(self):
        with patch("common.FloxyDatabase.FloxyDatabase.get_instance") as mock_get:
            mock_db = mock_get.return_value
            StartupTracker.record_shutdown()
        mock_db.add_history_entry.assert_called_once()
        args, _ = mock_db.add_history_entry.call_args
        assert args[0] == "system"
        assert args[1] == "Server is shutting down."
        assert isinstance(args[2], float)
        assert args[3] == "shutdown"

    def test_db_failure_is_swallowed(self):
        with patch("common.FloxyDatabase.FloxyDatabase.get_instance",
                   side_effect=RuntimeError("db unavailable")):
            StartupTracker.record_shutdown()


@pytest.mark.unit
class TestGetCommitsSince:
    def test_returns_split_commits(self):
        fake = subprocess.CompletedProcess(
            args=["git"], returncode=0,
            stdout="abc1234 first\ndef5678 second\n", stderr=""
        )
        with patch("assistant.agents.CodeQualityMaintainer.CodeQualityMaintainer._run_subprocess", return_value=fake):
            commits = StartupTracker.get_commits_since(1700000000.0)
        assert commits == ["abc1234 first", "def5678 second"]

    def test_empty_output_returns_empty_list(self):
        fake = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="", stderr=""
        )
        with patch("assistant.agents.CodeQualityMaintainer.CodeQualityMaintainer._run_subprocess", return_value=fake):
            assert StartupTracker.get_commits_since(1700000000.0) == []

    def test_nonzero_returncode_returns_empty_list(self):
        fake = subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="fatal: bad"
        )
        with patch("assistant.agents.CodeQualityMaintainer.CodeQualityMaintainer._run_subprocess", return_value=fake):
            assert StartupTracker.get_commits_since(1700000000.0) == []

    def test_subprocess_exception_returns_empty_list(self):
        with patch("assistant.agents.CodeQualityMaintainer.CodeQualityMaintainer._run_subprocess",
                   side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60)):
            assert StartupTracker.get_commits_since(1700000000.0) == []

    def test_passes_since_epoch_to_git(self):
        fake = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
        with patch("assistant.agents.CodeQualityMaintainer.CodeQualityMaintainer._run_subprocess",
                   return_value=fake) as mock_run:
            StartupTracker.get_commits_since(1700000000.5)
        called_args = mock_run.call_args[0][0]
        assert called_args[0] == "git"
        assert called_args[1] == "log"
        assert called_args[2] == "--since=@1700000000"
        assert "--" not in called_args   # the repository is the server tree: no pathspec
