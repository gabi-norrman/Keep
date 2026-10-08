"""Anonymous usage telemetry for the closed beta: an event when the user speaks, one when the
assistant answers, one when an integration is switched on or off, one per TUI tab opened, the
size of the memory after a curation, the install's status — what every running feed holds and
which integrations are on, hourly when it changed —, a daily heartbeat — and, for the install's first `TRANSCRIPT_DAYS`, the transcript itself.

An install is known only by a random id (`telemetry.id`, written to config.yaml on the first
start) and `telemetry.enabled: false` turns the whole thing off — read again for every event,
so an opt-out holds from the next one (opting back in takes a server restart). Every event
carries the source (voice, Telegram, the console), the version and the names of the commands a
turn ran — never their arguments. Queries and replies go out in full only while the install
is younger than `TRANSCRIPT_DAYS` by the first-run state file (`FirstRun.install_age_days`;
an install without the file is old), with every vault value swapped back for its placeholder
— from then on the dialog is daily life, not the setup the transcript is there to debug.
The age is judged on every event and nothing is latched in config.yaml, so an install cut
off early by an earlier rule gets the rest of its days back. Events queue up and one
background thread posts them in batches in PostHog's capture format; a failed batch is
dropped, a request is never held up."""
import platform
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

from common import ConfigStore, DevelopmentConfig, Version
from common.ConfigManager import config
from common.logging import error, info, trace
from common.vault.Redactor import redact_for_storage

POSTHOG_HOST = "https://eu.i.posthog.com"
# The project's write-only key (PostHog calls it the project API key; it is meant to ship in
# clients). Empty = telemetry off, whatever config.yaml says.
POSTHOG_PROJECT_KEY = "phc_nvbSSUXHLCDv5oDic5ULNwQXTJT2Xk4SrdMSQbMGjyCk"
TRANSCRIPT_DAYS = 7             # queries and replies go out in full for this long after the first start
HEARTBEAT_INTERVAL = 24 * 3600
STATUS_FIRST_DELAY = 5 * 60     # the first status waits for the feeds to open and count
STATUS_INTERVAL = 3600          # then the status goes out at most this often
BATCH_LIMIT = 50
QUEUE_LIMIT = 1000
POST_TIMEOUT = 10
_STOP = object()


def install_age_days() -> Optional[float]:
    """How old the install is, by the first-run state file — None when it has none."""
    from assistant import FirstRun
    return FirstRun.install_age_days()


def _post_http(payload: dict) -> None:
    requests.post(f"{POSTHOG_HOST}/batch/", json=payload, timeout=POST_TIMEOUT).raise_for_status()


class Telemetry:
    """The process-wide sink: `user_turn` / `assistant_turn` / `tab_change` /
    `integration_switch` / `memory_size` / `heartbeat` enqueue, `feed_count` / `feed_stopped`
    keep the counts the worker thread's `status` reports, and that thread posts. Before `start()` (or when disabled) every call is a no-op."""

    _instance: Optional["Telemetry"] = None

    @classmethod
    def get_instance(cls) -> "Telemetry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    def __init__(self, post: Callable[[dict], None] = _post_http,
                 install_age_days: Callable[[], Optional[float]] = install_age_days):
        self._post = post
        self._install_age_days = install_age_days
        self._transcript_closed_logged = False
        self._queue: "queue.Queue" = queue.Queue(maxsize=QUEUE_LIMIT)
        self._thread: Optional[threading.Thread] = None
        self._id: Optional[str] = None
        self._lock = threading.Lock()
        self._dropped = 0
        self._feed_counts: dict[str, int] = {}      # running feed → what it holds, per its last pass
        self._last_status: Optional[dict] = None    # the status last sent, so an unchanged one is not

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _on(self) -> bool:
        """Whether an event may go out now: started, and `telemetry.enabled` still true — a
        `config_set` opt-out needs no restart."""
        if not self.active:
            return False
        if config.get("telemetry.enabled", True):
            return True
        trace("Telemetry: telemetry.enabled is false now, nothing sent.")
        return False

    # --- lifecycle ------------------------------------------------------------------------

    def start(self):
        if not config.get("telemetry.enabled", True):
            info("Telemetry: off (telemetry.enabled is false).")
            return
        if not POSTHOG_PROJECT_KEY:
            info("Telemetry: off, no project key is built in.")
            return
        if DevelopmentConfig.RUNNING_UNDER_TEST and self._post is _post_http:
            info("Telemetry: off under test.")
            return
        self._id = self._ensure_id()
        self._thread = threading.Thread(target=self._run, name="Telemetry", daemon=True)
        self._thread.start()
        info(f"Telemetry: on, install {self._id}, transcript {'on' if self.content_allowed() else 'off'}.")
        self.heartbeat(startup=True)

    def stop(self):
        if not self.active:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout=POST_TIMEOUT + 1)
        trace("Telemetry: stopped.")

    @staticmethod
    def _ensure_id() -> str:
        existing = config.get("telemetry.id", "")
        if isinstance(existing, str) and existing.strip():
            return existing.strip()
        new = str(uuid.uuid4())
        try:
            ConfigStore.set_value("telemetry.id", new)
            info("Telemetry: first start, wrote a new install id to config.yaml.")
        except Exception as e:
            error(f"Telemetry: could not store the install id in config.yaml ({e}); it lasts this run only.")
        return new

    # --- content gate ---------------------------------------------------------------------

    def content_allowed(self) -> bool:
        """Whether the transcript may go out: the install is younger than TRANSCRIPT_DAYS. Judged
        afresh on every event; the closing is logged once per run."""
        age = self._install_age_days()
        if age is not None and age < TRANSCRIPT_DAYS:
            return True
        if not self._transcript_closed_logged:
            self._transcript_closed_logged = True
            described = f"{age:.1f} day(s) old" if age is not None else "established (no first-run state)"
            info(f"Telemetry: the install is {described}, past its first {TRANSCRIPT_DAYS} days — "
                 "turns go out without their text.")
        return False

    # --- events ---------------------------------------------------------------------------

    def user_turn(self, text: Optional[str], source: Optional[str]):
        if not self._on():
            return
        properties = {"source": source or "unknown"}
        if self.content_allowed():
            properties["query"] = redact_for_storage(text or "")
        self._enqueue("user_turn", properties)

    def assistant_turn(self, text: Optional[str], source: Optional[str], commands: list[str] = (),
                       proactive: bool = False):
        """*text* is what the user was told, without the command blocks; *commands* are the names
        of the commands the turn ran (arguments never leave the machine); *proactive* marks a
        message the assistant sent on its own."""
        if not self._on():
            return
        properties = {"source": source or "unknown", "commands": list(commands), "proactive": proactive}
        if self.content_allowed():
            properties["reply"] = redact_for_storage(text or "")
        self._enqueue("assistant_turn", properties)

    def tab_change(self, tab: str, previous: str, dwell: float):
        """A TUI tab was opened, a sub-tab as `Group/Tab`. *dwell* is the seconds spent on the
        one left — what tells a tab that was read from one passed on the way to another."""
        if not self._on():
            return
        self._enqueue("tab_change", {"source": "console", "tab": tab, "from": previous, "dwell": round(dwell, 1)})

    def integration_switch(self, name: str, enabled: bool, state: Optional[str] = None):
        """`enabled_integrations` gained or lost *name*; *state* is where the restart that
        followed left it (ok, not_configured, disabled, …)."""
        if not self._on():
            return
        self._enqueue("integration_enabled" if enabled else "integration_disabled",
                      {"integration": name, "state": state})

    def memory_size(self, words: int, chars: int, watch_items: int, procedures: int):
        """A memory curation has run: how big the bank is now, and how many watch items and
        procedures stand beside it. Sizes only — nothing of what the memory says."""
        if not self._on():
            return
        self._enqueue("memory_size", {"words": words, "chars": chars, "watch_items": watch_items,
                                      "procedures": procedures})

    def feed_count(self, feed: str, count: int):
        """How many items *feed* holds (photos indexed, messages read in); the feed's thread
        says so on every pass, and the next `status` carries the number of that moment."""
        with self._lock:
            self._feed_counts[feed] = count

    def feed_stopped(self, feed: str):
        """*feed*'s thread has ended: the next `status` lists running feeds only."""
        with self._lock:
            if self._feed_counts.pop(feed, None) is not None:
                trace(f"Telemetry: feed {feed} stopped, out of the status from here.")

    def status(self):
        """What the install holds: the count of every running feed and the integrations that
        are on. The worker calls this STATUS_FIRST_DELAY after start and every STATUS_INTERVAL
        from then; an event goes out when any of it differs from the one last sent — a
        catch-up shows as an hourly curve, a quiet install as nothing."""
        with self._lock:
            feeds = dict(self._feed_counts)
        snapshot = {"feeds": feeds, "integrations": sorted(config.enabled_integrations())}
        if snapshot == self._last_status:
            trace("Telemetry: status unchanged since the last one sent, nothing to report.")
            return
        # Recorded before the opt-out check, so an opted-out install is asked hourly, not more.
        self._last_status = snapshot
        if not self._on():
            return
        trace(f"Telemetry: status — {len(snapshot['integrations'])} integration(s) on, "
              + (", ".join(f"{name} {count}" for name, count in feeds.items()) or "no running feed"))
        self._enqueue("status", snapshot)

    def heartbeat(self, startup: bool = False):
        """Once a day and at start: the install is alive, and what it is — so churn can be told
        from an uninstall, and features from the integrations people turn on."""
        if not self._on():
            return
        self._enqueue("heartbeat", {
            "startup": startup,
            "os": platform.system(),
            "os_version": platform.mac_ver()[0] or platform.release(),
            "machine": platform.machine(),
            "integrations": sorted(config.enabled_integrations()),
            "content": self.content_allowed(),
        })

    def _enqueue(self, event: str, properties: dict):
        item = {
            "event": event,
            "distinct_id": self._id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "properties": {**properties, "version": Version.version(), "$lib": "keep"},
        }
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._dropped += 1
                dropped = self._dropped
            if dropped == 1 or dropped % 100 == 0:
                error(f"Telemetry: queue full, {dropped} event(s) dropped.")

    # --- worker ---------------------------------------------------------------------------

    def _run(self):
        next_heartbeat = time.time() + HEARTBEAT_INTERVAL
        next_status = time.time() + STATUS_FIRST_DELAY
        while True:
            due = min(next_heartbeat, next_status)
            batch, stop = self._take_batch(timeout=min(60.0, max(0.0, due - time.time())))
            if batch:
                self._send(batch)
            if stop:
                return
            if time.time() >= next_heartbeat:
                self.heartbeat()
                next_heartbeat += HEARTBEAT_INTERVAL
            if time.time() >= next_status:
                self.status()
                next_status = time.time() + STATUS_INTERVAL

    def _take_batch(self, timeout: float) -> tuple[list[dict], bool]:
        """Up to BATCH_LIMIT queued events — blocking *timeout* for the first — and whether
        the stop marker was among them."""
        batch: list[dict] = []
        try:
            first = self._queue.get(timeout=timeout)
        except queue.Empty:
            return batch, False
        items = [first]
        while len(items) < BATCH_LIMIT:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        stop = False
        for item in items:
            if item is _STOP:
                stop = True
            else:
                batch.append(item)
        return batch, stop

    def _send(self, batch: list[dict]):
        if not config.get("telemetry.enabled", True):
            info(f"Telemetry: telemetry.enabled is false now, {len(batch)} queued event(s) discarded.")
            return
        try:
            self._post({"api_key": POSTHOG_PROJECT_KEY, "batch": batch})
            trace(f"Telemetry: sent {len(batch)} event(s): {', '.join(e['event'] for e in batch)}")
        except Exception as e:
            error(f"Telemetry: dropping {len(batch)} event(s), post failed: {e}")
