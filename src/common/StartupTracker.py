import html
import json
import os
import time
from datetime import datetime

from common.logging import info, error, trace
from common.Paths import CACHE_DIR

STATE_FILE = CACHE_DIR / "last_startup.json"


class StartupTracker:
    """Persists the server's last startup time and builds the startup/shutdown history messages.

    The startup timestamp lives in cache/last_startup.json; on boot the commits made
    since then are pulled from git so the assistant can see what changed.
    """

    @staticmethod
    def read_previous_startup() -> float | None:
        if not STATE_FILE.exists():
            info("StartupTracker: no previous startup record found, this looks like a first boot.")
            return None
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            ts = float(data.get("started_at"))
            trace(f"StartupTracker: previous startup was at {datetime.fromtimestamp(ts).isoformat(timespec='seconds')}.")
            return ts
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            error(f"StartupTracker: failed to read state file ({e}); treating as first boot.")
            return None

    @staticmethod
    def write_current_startup(ts: float):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = STATE_FILE.with_suffix(".json.tmp")
            with open(tmp_path, "w") as f:
                json.dump({"started_at": ts}, f)
            os.replace(tmp_path, STATE_FILE)
            trace(f"StartupTracker: recorded current startup time at {STATE_FILE}.")
        except OSError as e:
            error(f"StartupTracker: failed to write state file: {e}")

    @staticmethod
    def get_commits_since(ts: float) -> list[str]:
        from assistant.agents.CodeQualityMaintainer import CodeQualityMaintainer, GIT_TIMEOUT
        try:
            result = CodeQualityMaintainer._run_subprocess(
                ["git", "log", f"--since=@{int(ts)}", "--pretty=format:%h %s"],
                "startup-log",
                GIT_TIMEOUT,
            )
        except Exception as e:
            error(f"StartupTracker: git log failed: {e}")
            return []

        if result.returncode != 0:
            error(f"StartupTracker: git log returned {result.returncode}: {result.stderr.strip() if result.stderr else ''}")
            return []

        output = (result.stdout or "").strip()
        if not output:
            trace("StartupTracker: no commits since last start.")
            return []

        commits = output.splitlines()
        info(f"StartupTracker: {len(commits)} commit(s) since last start.")
        return commits

    @staticmethod
    def record_shutdown():
        """Persist a shutdown marker directly to the history DB.

        Writes via FloxyDatabase rather than the History prompt section so this is safe
        to call from atexit, after section monitoring threads may have stopped.
        """
        try:
            from .FloxyDatabase import FloxyDatabase
            FloxyDatabase.get_instance().add_history_entry(
                "system", "Server is shutting down.", time.time(), "shutdown"
            )
            info("StartupTracker: recorded shutdown marker in history.")
        except Exception as e:
            error(f"StartupTracker: failed to record shutdown marker: {e}")

    @staticmethod
    def build_history_message(commits: list[str], previous_ts: float | None) -> str:
        if previous_ts is None:
            return "Server started up successfully."
        if not commits:
            return "Server started up successfully. No changes since last start."
        change_log = "\n".join(commits)
        return f"Server started up successfully. Change log since last start:\n{change_log}"

    @staticmethod
    def build_announcement(commits: list[str], version: str) -> str:
        """What the owner's chats are told once the server is up: that it is running again,
        on which version, and the subjects of the commits picked up since the last start.
        In the Notifier's HTML dialect, so the subjects are escaped."""
        up = f"Server is up again — {html.escape(version)}."
        if not commits:
            return f"{up} No new commits since the last start."
        subjects = "\n".join(f"• {html.escape(c.partition(' ')[2] or c, quote=False)}" for c in commits)
        count = f"{len(commits)} new commit{'s' if len(commits) != 1 else ''}"
        return f"{up} {count} since the last start:\n{subjects}"
