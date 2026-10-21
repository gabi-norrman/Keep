"""Tracks how far a local model has got with a prompt's prefill, for the "Prefilling…" bar of the chat UIs.

A local server (Ollama, ds4-server) says nothing on the wire until the first token, so the only
view into a long prefill is its log file: one line at the start of the request says how much of
the prompt the KV cache covered and how many tokens are left to prefill, and some engines log
incremental progress after that. One `PrefillTracker` per backend class holds the state of the
request in flight; the backend plugs in a `LogFormat` that reads its own log lines, points the
tracker at the log (`use_log`, a local file or one read over ssh when the server is on another
machine), and brackets each request with `begin` / `first_token` / `end`.

The estimate: 0 until the start line is read (nothing to draw), 100 at once on a full cache hit,
the engine's own progress lines when it logs them, and otherwise elapsed time against a rolling
prefill rate (`observe_rate` refines it after each reply) — capped at 99 until the first token
confirms the prefill is over. Each server runs one request at a time, but ours overlap: a
pre-heat fired at the end of one turn is often still prefilling when the next user request goes
out, and the first-run pipeline or the autonomous check may hold the server while the user asks.
So the tracker keeps every request in flight, in the order they were sent — the order the server
takes them in — and the bar follows the first one that has no token yet: that is the request the
server is prefilling, or will prefill next, and whatever it is, it is what the user's reply is
waiting for (a request queued behind another's generation reads 0 until its own workload line
appears). `PrefillTracker.current_progress()` is what the interfaces poll: the backend with a
user request in flight, else any backend with a request.
"""
import os
import re
import shlex
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from common.logging import info, trace

LOCAL_HOSTS = {None, '', 'localhost', '127.0.0.1', '::1'}


def is_local_host(hostname: str | None) -> bool:
    """Whether a base_url's hostname is this machine — the log is then a file, not an ssh read."""
    return hostname in LOCAL_HOSTS


class LogSource:
    """A server's log file: on this machine, or on the serving host read over ssh.

    The ssh connections are multiplexed over a persistent control socket, so after the first call
    each read costs tens of milliseconds. Any ssh failure disables further attempts for a cooldown
    instead of stalling the request path on repeated connect timeouts.
    """
    _SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
                 "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/floxy-log-ssh-%C",
                 "-o", "ControlPersist=600"]
    _FAILURE_COOLDOWN_S = 60.0
    _MAX_READ_BYTES = 1_000_000

    def __init__(self, label: str, path: str, ssh_host: str | None = None):
        self.label = label
        self.path = path
        self.ssh_host = ssh_host
        self._disabled_until = 0.0

    def __eq__(self, other):
        return isinstance(other, LogSource) and (self.path, self.ssh_host) == (other.path, other.ssh_host)

    def describe(self) -> str:
        return f"{self.path} via ssh {self.ssh_host}" if self.ssh_host else self.path

    def size(self) -> int | None:
        """Log size in bytes, 0 if the file doesn't exist, None if it is unreachable."""
        if self.ssh_host is None:
            try:
                path = os.path.expanduser(self.path)
                return os.path.getsize(path) if os.path.exists(path) else 0
            except OSError:
                return None
        out = self._run(f"wc -c 2>/dev/null < {self._remote_path()} || echo MISSING")
        if out is None:
            return None
        out = out.strip()
        if out == "MISSING":
            trace(f"{self.label} log {self.ssh_host}:{self.path} does not exist (yet).")
            return 0
        try:
            return int(out)
        except ValueError:
            self._disable(f"unexpected wc output {out!r}")
            return None

    def read_from(self, offset: int) -> str | None:
        """Log content from byte offset to EOF (capped), None if empty or unreachable."""
        if self.ssh_host is None:
            try:
                path = os.path.expanduser(self.path)
                if not os.path.exists(path) or os.path.getsize(path) <= offset:
                    return None
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(offset)
                    return f.read()
            except OSError:
                return None
        out = self._run(f"(tail -c +{offset + 1} {self._remote_path()} 2>/dev/null || true)"
                        f" | head -c {self._MAX_READ_BYTES}")
        return out or None

    def _remote_path(self) -> str:
        """The path quoted for the remote shell, a leading `~/` left to that shell's $HOME."""
        if self.path.startswith("~/"):
            return '"$HOME"/' + shlex.quote(self.path[2:])
        return shlex.quote(self.path)

    def _run(self, remote_cmd: str) -> str | None:
        if time.monotonic() < self._disabled_until:
            return None
        try:
            result = subprocess.run(["ssh", *self._SSH_OPTS, self.ssh_host, remote_cmd],
                                    capture_output=True, text=True, errors='replace', timeout=5)
            if result.returncode != 0:
                self._disable(f"exit {result.returncode}: {result.stderr.strip()}")
                return None
            return result.stdout
        except (subprocess.SubprocessError, OSError) as e:
            self._disable(str(e))
            return None

    def _disable(self, reason: str):
        self._disabled_until = time.monotonic() + self._FAILURE_COOLDOWN_S
        info(f"{self.label} log via ssh {self.ssh_host} failed ({reason}) — "
             f"disabling prefill diagnostics for {self._FAILURE_COOLDOWN_S:.0f}s.")


class LogFormat(ABC):
    """How one server's log tells the two things the tracker needs, from the text logged since a
    request started: the request's workload and, for engines that log it, its progress."""

    @abstractmethod
    def work(self, text: str) -> tuple[int, int] | None:
        """(cached, remaining) tokens of the request — its first workload line in `text` — or
        None while the server has not logged one."""

    @abstractmethod
    def progress(self, text: str) -> tuple[int, int] | None:
        """(processed, total) of the latest progress line in `text`, None when there is none."""


@dataclass
class _Request:
    """One request in flight, with what its log lines have said so far."""
    token: int
    interactive: bool          # a user request, not a pre-heat / background call
    start_ts: float
    anchor: int | None         # log EOF when the request was sent; None = log unreachable for it
    first_token: bool = False
    work: tuple[int, int] | None = None       # (cached, remaining) once the workload line is logged
    progress: tuple[int, int] | None = None   # (processed, total) of the latest progress line
    read_ts: float = 0.0


class PrefillTracker:
    """The prefill state of one local backend's requests in flight (see the module docstring)."""
    POLL_INTERVAL_S = 2.0   # log reads between polls: over ssh each is a round trip, and new lines are rare
    _trackers: list["PrefillTracker"] = []

    def __init__(self, label: str, log_format: LogFormat, seed_tps: float):
        self.label = label
        self.format = log_format
        self.tps = seed_tps  # rolling estimate of the prefill rate, tokens/s; the seed stands until a reply refines it
        self.log: LogSource | None = None
        self._lock = threading.Lock()
        self._requests: list[_Request] = []   # in the order sent — the order the server serves them
        self._last_token = 0
        PrefillTracker._trackers.append(self)

    def use_log(self, path: str, ssh_host: str | None = None):
        """Point the tracker at the server's log — a file here, or `path` on `ssh_host`."""
        source = LogSource(self.label, path, ssh_host)
        if self.log == source:
            return
        self.log = source
        info(f"{self.label} prefill diagnostics: reading {source.describe()}.")

    def begin(self, interactive: bool = True) -> int:
        """Register a request about to be sent and hand back its token."""
        # The anchor is fetched before taking the lock: over ssh it can take a moment and the
        # progress polls must not block on it. The scan from it stays non-destructive, so every
        # poll and the done-time read see the same lines.
        anchor = self.log.size() if self.log is not None else None
        if anchor is None:
            trace(f"{self.label} prefill: log unreachable — prefill diagnostics disabled for this request.")
        with self._lock:
            self._last_token += 1
            token = self._last_token
            self._requests.append(_Request(token, interactive, time.monotonic(), anchor))
            queued = len(self._requests) - 1
        trace(f"{self.label} prefill: started (#{token}, {'user request' if interactive else 'background'}"
              f"{f', {queued} ahead of it' if queued else ''}).")
        return token

    def first_token(self, token: int | None):
        """The request's first generated token means its prefill is over."""
        with self._lock:
            request = self._find(token)
            if request is not None and not request.first_token:
                request.first_token = True
                trace(f"{self.label} prefill: first token received (prefill #{token} complete).")

    def end(self, token: int | None):
        """The request is over; the next one in line, if any, is what the bar follows."""
        with self._lock:
            request = self._find(token)
            if request is not None:
                self._requests.remove(request)

    def observe_rate(self, tokens: int, seconds: float):
        """Refine the prefill-rate estimate (EMA) from a finished request. Near-full cache hits
        are skipped: a handful of tokens over a tiny duration is noise."""
        if tokens <= 100 or seconds <= 0:
            return
        observed = tokens / seconds
        self.tps = 0.5 * self.tps + 0.5 * observed
        trace(f"{self.label} prefill rate estimate updated: {self.tps:.1f} t/s (observed {observed:.1f} t/s)")

    def work(self, token: int | None, force: bool = False) -> tuple[int, int] | None:
        """The request's (cached, remaining) tokens once its workload line is logged, else None.
        `force` reads the log now instead of waiting for the next poll slot — the done-time read."""
        with self._lock:
            request = self._find(token)
        return self._poll(request, force)[0] if request is not None else None

    def active(self) -> bool:
        return bool(self._requests)

    def interactive(self) -> bool:
        """Whether a user request is among the requests in flight."""
        return any(r.interactive for r in self._requests)

    def progress(self) -> float | None:
        """Estimated prefill completion (0-100) of the request the server is on, None when no
        request is in flight. 100 when every request in flight is past its prefill."""
        with self._lock:
            if not self._requests:
                return None
            head = next((r for r in self._requests if not r.first_token), None)
        if head is None:
            return 100.0
        work, progress = self._poll(head)
        if work is None:
            return 0.0   # the server has not logged the workload yet — nothing to draw
        _cached, remaining = work
        if remaining <= 0:
            return 100.0   # a full cache hit: no prefill work
        if progress and progress[1] > 0:
            processed, total = progress
            return min(99.0, processed / total * 100.0)
        eta = remaining / self.tps if self.tps > 0 else 0.0
        if eta <= 0:
            return 99.0
        return min(99.0, (time.monotonic() - head.start_ts) / eta * 100.0)

    def _find(self, token: int | None) -> _Request | None:
        return next((r for r in self._requests if r.token == token), None)

    def _poll(self, request: _Request, force: bool = False) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        """One throttled log read for both answers. The read runs OUTSIDE the lock — over ssh it
        can take seconds, and `first_token` and every TUI frame would stall behind it."""
        with self._lock:
            now = time.monotonic()
            due = force or now - request.read_ts >= self.POLL_INTERVAL_S
            if due:
                request.read_ts = now
            anchor, work, progress = request.anchor, request.work, request.progress
        if not due or anchor is None:
            return work, progress
        text = self.log.read_from(anchor) if self.log is not None else None
        if not text:
            return work, progress
        try:
            new_work = work or self.format.work(text)
            new_progress = self.format.progress(text) or progress
        except Exception as e:
            trace(f"{self.label} prefill: log lines not understood ({e})")
            return work, progress
        with self._lock:
            if request.work is None:
                request.work = new_work
            if new_progress is not None:
                request.progress = new_progress
            return request.work, request.progress

    @classmethod
    def current_progress(cls) -> float | None:
        """The progress of what a user is waiting for — the backend with a user request in
        flight, else any backend with a request — or None when the local backends are idle."""
        active = [t for t in cls._trackers if t.active()]
        if not active:
            return None
        tracker = next((t for t in active if t.interactive()), active[0])
        return tracker.progress()


def parse_last(pattern: str, text: str) -> tuple[int, int] | None:
    """The last match of a two-group integer pattern in `text`, as a pair — the shape of every
    progress line — or None."""
    matches = re.findall(pattern, text)
    if not matches:
        return None
    a, b = matches[-1]
    return int(a), int(b)
