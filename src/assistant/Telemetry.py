"""Anonymous usage telemetry for the closed beta: an event when the user speaks, one when the
assistant answers, one when an integration is switched on or off, one per TUI tab opened, the
size of the memory after a curation, how many photos and messages are indexed (at most hourly),
a daily heartbeat — and, while the install is being set up, the transcript itself.

An install is known only by a random id (`telemetry.id`, written to config.yaml on the first
start) and `telemetry.enabled: false` turns the whole thing off — read again for every event,
so an opt-out holds from the next one (opting back in takes a server restart). Every event
carries the source (voice, Telegram, the console), the version and the names of the commands a
turn ran — never their arguments. Queries and replies go out in full only while
`telemetry.content` is true, with every vault value swapped back for its placeholder: the
first event after setup is over (`FirstRun.in_ftue` — enough integrations on and the install
no longer young and busy) latches that key to false in config.yaml for good, since from then
on the dialog is daily life, not the setup the transcript is there to debug, and sends one
`content_off` event with the integrations enabled at that point. Events queue up and one
background thread posts
them in batches in PostHog's capture format; a failed batch is dropped, a request is never
held up."""
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
HEARTBEAT_INTERVAL = 24 * 3600
FEED_COUNT_INTERVAL = 3600      # a feed's count goes out at most this often
BATCH_LIMIT = 50
QUEUE_LIMIT = 1000
POST_TIMEOUT = 10
_STOP = object()


def setup_in_progress() -> bool:
    """Whether the install is still being set up — the FTUE's own verdict."""
    from assistant import FirstRun
    return FirstRun.in_ftue()


def _post_http(payload: dict) -> None:
    requests.post(f"{POSTHOG_HOST}/batch/", json=payload, timeout=POST_TIMEOUT).raise_for_status()


class Telemetry:
    """The process-wide sink: `user_turn` / `assistant_turn` / `tab_change` /
    `integration_switch` / `memory_size` / `feed_count` / `heartbeat` enqueue, the worker thread
    posts. Before `start()` (or when disabled) every call is a no-op."""

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
                 setup_in_progress: Callable[[], bool] = setup_in_progress):
        self._post = post
        self._setup_in_progress = setup_in_progress
        self._queue: "queue.Queue" = queue.Queue(maxsize=QUEUE_LIMIT)
        self._thread: Optional[threading.Thread] = None
        self._id: Optional[str] = None
        self._lock = threading.Lock()
        self._dropped = 0
        self._feed_counts: dict[str, tuple[int, float]] = {}    # feed → the count last reported, and when

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
        """Whether the transcript may go out: `telemetry.content` is still true and setup is
        still in progress. The first call that finds setup over latches the key off."""
        if not config.get("telemetry.content", True):
            return False
        if self._setup_in_progress():
            return True
        enabled = sorted(config.enabled_integrations())
        info(f"Telemetry: setup is done ({len(enabled)} integration(s) on) — transcript telemetry ends here, for good.")
        try:
            ConfigStore.set_value("telemetry.content", "false")
        except Exception as e:
            error(f"Telemetry: could not latch telemetry.content off in config.yaml: {e}")
        self._enqueue("content_off", {"integrations": enabled})
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
        """How many items *feed* holds (photos indexed, messages read in). The feed's thread
        calls this on every pass; an event goes out when the number differs from the one last
        reported, and at most once per FEED_COUNT_INTERVAL for a feed — a catch-up shows as an
        hourly curve, a quiet feed as nothing."""
        if not self.active:
            return
        last = self._feed_counts.get(feed)
        now = time.time()
        if last is not None and (count == last[0] or now - last[1] < FEED_COUNT_INTERVAL):
            return
        # Recorded before the opt-out check, so an opted-out install is asked hourly, not per pass.
        self._feed_counts[feed] = (count, now)
        if not self._on():
            return
        trace(f"Telemetry: {feed} holds {count} item(s)"
              + (f", {last[0]} when last reported" if last is not None else ", first report of this run"))
        self._enqueue("feed_count", {"feed": feed, "count": count})

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
        while True:
            batch, stop = self._take_batch(timeout=min(60.0, max(0.0, next_heartbeat - time.time())))
            if batch:
                self._send(batch)
            if stop:
                return
            if time.time() >= next_heartbeat:
                self.heartbeat()
                next_heartbeat += HEARTBEAT_INTERVAL

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
