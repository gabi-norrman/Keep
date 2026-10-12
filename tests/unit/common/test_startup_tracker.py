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


@pytest.mark.unit
class TestAnnouncedRelease:
    def test_none_before_any_release_was_announced(self, temp_state_file):
        StartupTracker.write_current_startup(1.0)
        assert StartupTracker.read_announced_release() is None

    def test_a_later_start_keeps_the_announced_release(self, temp_state_file):
        StartupTracker.write_current_startup(1.0, "v2026.10.243")
        StartupTracker.write_current_startup(2.0)
        assert StartupTracker.read_announced_release() == "v2026.10.243"
        assert StartupTracker.read_previous_startup() == 2.0

    def test_a_newer_release_replaces_it(self, temp_state_file):
        StartupTracker.write_current_startup(1.0, "v2026.10.243")
        StartupTracker.write_current_startup(2.0, "v2026.11.250")
        assert StartupTracker.read_announced_release() == "v2026.11.250"


@pytest.mark.unit
class TestUnannouncedReleaseNotes:
    NOTE = "<b>Spotify is back.</b>"

    def test_the_first_start_on_a_release_gets_its_note(self, temp_state_file):
        with patch.object(startup_tracker_module.Version, "release_notes", return_value=self.NOTE), \
             patch.object(startup_tracker_module.Version, "version", return_value="v2026.10.243"):
            assert StartupTracker.unannounced_release_notes() == self.NOTE

    def test_a_restart_on_an_announced_release_gets_nothing(self, temp_state_file):
        StartupTracker.write_current_startup(1.0, "v2026.10.243")
        with patch.object(startup_tracker_module.Version, "release_notes", return_value=self.NOTE), \
             patch.object(startup_tracker_module.Version, "version", return_value="v2026.10.243"):
            assert StartupTracker.unannounced_release_notes() == ""

    def test_an_untagged_commit_gets_nothing(self, temp_state_file):
        with patch.object(startup_tracker_module.Version, "release_notes", return_value=""):
            assert StartupTracker.unannounced_release_notes() == ""

@pytest.mark.unit
class TestBuildHistoryMessage:
    def test_the_whats_new_follows_the_change_log_without_its_markup(self):
        with patch.object(startup_tracker_module.Version, "version", return_value="v2026.10.243"):
            message = StartupTracker.build_history_message(
                ["abc feat: x"], 1.0, "<b>Spotify &amp; radio are back.</b>\n\n<i>Nothing else.</i>")
        assert "abc feat: x" in message
        assert "v2026.10.243" in message
        assert "Spotify & radio are back.\n\nNothing else." in message
        assert "<b>" not in message and "&amp;" not in message

    def test_with_commits(self):
        commits = ["abc1234 first commit", "def5678 second commit"]
        msg = StartupTracker.build_history_message(commits, 1700000000.0)
        assert msg.startswith("Server started up successfully. Change log since last start:\n")
        assert "abc1234 first commit" in msg
        assert "def5678 second commit" in msg


@pytest.mark.unit
class TestBuildAnnouncement:
    def test_a_release_note_replaces_the_commit_list_and_keeps_its_markup(self):
        text = StartupTracker.build_announcement(["abc feat: x"], "Keep v2026.10.243", "<b>Spotify is back.</b>")
        assert "<b>Spotify is back.</b>" in text
        assert "Keep v2026.10.243" in text
        assert "feat: x" not in text

    def test_lists_the_subjects_without_their_hashes(self):
        text = StartupTracker.build_announcement(
            ["abc1234 fix: the porch light", "def5678 feat: a new tab"], "Keep v2026.10.7")
        lines = text.splitlines()
        assert "Keep v2026.10.7" in lines[0] and "2 new commits" in lines[0]
        assert [line.lstrip("• ") for line in lines[1:]] == ["fix: the porch light", "feat: a new tab"]

    def test_one_commit_is_singular(self):
        first_line = StartupTracker.build_announcement(["abc1234 fix: x"], "Keep v1").splitlines()[0]
        assert "1 new commit " in first_line

    def test_no_commits_is_a_single_line(self):
        text = StartupTracker.build_announcement([], "Keep v2026.10.7")
        assert "Keep v2026.10.7" in text and "\n" not in text and "•" not in text

    def test_a_trial_note_closes_the_announcement_as_its_own_paragraph(self):
        text = StartupTracker.build_announcement(["abc1234 fix: x"], "Keep v1 trial/pr-9", trial_note="You are trying PR #9.")
        assert text.endswith("\n\nYou are trying PR #9.") and "fix: x" in text
        assert "You are trying" not in StartupTracker.build_announcement(["abc1234 fix: x"], "Keep v1")

    def test_subjects_are_escaped_for_the_chat_html_dialect(self):
        text = StartupTracker.build_announcement(["abc1234 fix: the <commands> block & its tail"], "Keep v1")
        assert "&lt;commands&gt; block &amp; its tail" in text


@pytest.mark.unit
class TestRecordShutdown:
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
