import time
from unittest.mock import MagicMock

import pytest

from interfaces.voice.microphones.local_outposts import AudioRecovery


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    """Isolate AudioRecovery: fresh per-process state, a temp marker file, no real
    Telegram alert, and a stubbed restart so nothing actually relaunches."""
    monkeypatch.setattr(AudioRecovery, "_restart_launched", False)
    monkeypatch.setattr(AudioRecovery, "STATE_FILE", tmp_path / "audio_restarts.json")
    monkeypatch.setattr(AudioRecovery, "LOCAL_DEVELOPMENT_MODE", False)
    alert = MagicMock()
    monkeypatch.setattr(AudioRecovery, "_alert", alert)
    restart = MagicMock()
    monkeypatch.setattr("assistant.agents.CodeQualityMaintainer.CodeQualityMaintainer.restart", restart)
    return restart, alert


@pytest.mark.unit
def test_triggers_restart_and_records_timestamp(recovery):
    restart, _ = recovery
    assert AudioRecovery.trigger("wedge") is True
    restart.assert_called_once()
    assert len(AudioRecovery._load_restart_times()) == 1


@pytest.mark.unit
def test_dev_mode_skips_restart(recovery, monkeypatch):
    restart, _ = recovery
    monkeypatch.setattr(AudioRecovery, "LOCAL_DEVELOPMENT_MODE", True)
    assert AudioRecovery.trigger("wedge") is False
    restart.assert_not_called()


@pytest.mark.unit
def test_duplicate_trigger_in_same_process_is_ignored(recovery):
    restart, _ = recovery
    assert AudioRecovery.trigger("wedge") is True
    # Second stall a few seconds later (before start.sh kills us) must not double-restart.
    assert AudioRecovery.trigger("wedge again") is False
    restart.assert_called_once()


@pytest.mark.unit
def test_loop_guard_suppresses_after_max_restarts(recovery):
    restart, alert = recovery
    now = time.time()
    AudioRecovery._save_restart_times([now - 10] * AudioRecovery.MAX_RESTARTS)
    assert AudioRecovery.trigger("wedge") is False
    restart.assert_not_called()
    alert.assert_called_once()  # user is alerted that auto-restart was suspended


@pytest.mark.unit
def test_old_restarts_outside_window_do_not_count(recovery):
    restart, _ = recovery
    stale = time.time() - (AudioRecovery.WINDOW_SECONDS + 60)
    AudioRecovery._save_restart_times([stale] * AudioRecovery.MAX_RESTARTS)
    assert AudioRecovery.trigger("wedge") is True
    restart.assert_called_once()
    # The stale entries are pruned; only the new one remains.
    assert len(AudioRecovery._load_restart_times()) == 1


@pytest.mark.unit
def test_alert_is_a_voice_system_note_and_never_blocks_the_restart(monkeypatch):
    """b4308ef7: the recovery alert lands in the dialog history as a system note from
    "voice" so the assistant knows a restart happened; a failing note is swallowed
    because it must never stop the restart itself."""
    note = MagicMock()
    monkeypatch.setattr(AudioRecovery, "system_note", note)
    AudioRecovery._alert("audio wedged")
    note.assert_called_once_with("voice", "audio wedged")

    note.side_effect = RuntimeError("history is gone")
    AudioRecovery._alert("still wedged")   # no raise
