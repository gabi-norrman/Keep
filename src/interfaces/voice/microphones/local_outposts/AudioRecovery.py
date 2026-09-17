"""Automatic recovery from an in-process audio (PortAudio/CoreAudio) wedge.

The playback-stall forensics in AudioDiagnostics decide WHETHER a stalled playback worker
is an in-process wedge (a fresh subprocess plays the default output fine → only THIS
process's PortAudio/CoreAudio session is poisoned, and a restart clears it) or a
system-wide one (coreaudiod itself wedged → a restart won't help). On the 2026-06-14
incident the in-process session wedged mid-evening and every TTS reply silently failed;
the process leaked 8 playback threads stuck inside CoreAudio and never self-healed until a
manual restart. This module turns that manual restart into an automatic one.

On the in-process verdict it restarts the server through the SAME start.sh path the manual
restart command uses (CodeQualityMaintainer.restart) — with a persisted rolling-window
loop guard so a wedge a restart can't actually fix (a mis-classified system-wide wedge)
can't send the server into a boot loop: after MAX_RESTARTS within WINDOW_SECONDS it stops
restarting and alerts instead.
"""

import json
import os
import threading
import time

from common.logging import info, error, note, trace
from assistant.SystemNote import system_note
from common.DevelopmentConfig import LOCAL_DEVELOPMENT_MODE
from common.Paths import CACHE_DIR

STATE_FILE = CACHE_DIR / "audio_restarts.json"

# Loop guard: at most MAX_RESTARTS audio-triggered restarts within WINDOW_SECONDS. If a
# wedge recurs beyond that, a restart clearly isn't fixing it (likely a system-wide
# coreaudiod wedge needing a reboot), so we stop and alert rather than boot-loop.
MAX_RESTARTS = 2
WINDOW_SECONDS = 900  # 15 minutes

_lock = threading.Lock()
# Set once we've launched a restart this process lifetime, so a second stall detected in
# the few seconds before start.sh kills us can't fire a duplicate restart.
_restart_launched = False


def _load_restart_times() -> list:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return [float(t) for t in data.get("restarts", [])]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []


def _save_restart_times(times: list):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = STATE_FILE.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump({"restarts": times}, f)
        os.replace(tmp_path, STATE_FILE)
    except OSError as e:
        error(f"AudioRecovery: failed to persist restart marker: {e}")


def _alert(message: str):
    """Best-effort system note so the user and the assistant know a recovery happened (or
    was suppressed). Never let a failed alert block the restart."""
    try:
        system_note("voice", message)
    except Exception as e:
        trace(f"AudioRecovery: alert failed: {e}")


def trigger(reason: str) -> bool:
    """Restart the server to clear a confirmed in-process audio wedge. Returns True if a
    restart was launched, False if it was skipped (dev mode, already launching, or the
    loop guard tripped). Safe to call from any thread."""
    global _restart_launched

    if LOCAL_DEVELOPMENT_MODE:
        info(f"AudioRecovery: in-process audio wedge detected ({reason}) but "
             f"LOCAL_DEVELOPMENT_MODE is on — not auto-restarting.")
        return False

    with _lock:
        if _restart_launched:
            info("AudioRecovery: a restart is already in progress — ignoring duplicate trigger.")
            return False

        now = time.time()
        recent = [t for t in _load_restart_times() if now - t < WINDOW_SECONDS]
        window_min = WINDOW_SECONDS // 60

        if len(recent) >= MAX_RESTARTS:
            error(f"AudioRecovery: audio wedged again ({reason}) but already restarted "
                  f"{len(recent)} time(s) in the last {window_min} min — NOT restarting "
                  f"again to avoid a boot loop. A restart clearly isn't clearing this; the "
                  f"wedge is likely system-wide (coreaudiod) and needs a reboot or manual "
                  f"intervention.")
            _alert(f"⚠️ Voice: audio output wedged again after {len(recent)} auto-restart(s) "
                   f"in {window_min} min. Auto-restart suspended to avoid a loop — a reboot "
                   f"or manual check is probably needed.")
            return False

        recent.append(now)
        _save_restart_times(recent)
        _restart_launched = True

    note(f"AudioRecovery: in-process audio wedge confirmed ({reason}) — restarting the "
         f"server to clear it (restart {len(recent)}/{MAX_RESTARTS} in the last "
         f"{window_min} min).")
    _alert(f"\U0001f501 Voice: detected a wedged audio output, the server is restarting to "
           f"recover ({reason}).")

    try:
        from assistant.agents.CodeQualityMaintainer import CodeQualityMaintainer
        CodeQualityMaintainer.restart(f"audio recovery: {reason}")
    except Exception as e:
        error(f"AudioRecovery: failed to launch restart: {e}")
        with _lock:
            _restart_launched = False
        return False
    return True
