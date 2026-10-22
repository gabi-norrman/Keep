"""The single-slot gate a local backend runs its background requests through: a background request waits for the idle slot, and a user request arriving mid-flight evicts it.

A local engine (Ollama, ds4-server) serves one request at a time and the model fills the
machine, so a background image call (photo indexing) competes with the user's next turn for
the one slot. The gate puts the user first: a background request waits until nothing
interactive is in flight and the engine has been idle for `idle_seconds`; an interactive
request arriving mid-image shuts the image request's socket — both engines drop a request whose
client is gone — and takes the slot within a fraction of a second. The evicted caller gets
LLMPreempted and resends when the slot is idle again.

One instance per backend class, shared by its tiers: the tiers share the engine and its one
slot. The backend wraps its interactive requests in `interactive_begin` / `interactive_end`,
its background request in `acquire` / `release`, and registers the raw connection it reads
the stream over with `register_connection` so the gate can shut its socket.
"""
import http.client
import socket
import threading
import time

from .AbstractLLM import LLMPreempted
from common.logging import info, trace


class BackgroundSlot:
    PREEMPT_UNWIND_TIMEOUT_S = 5.0
    DEFAULT_IDLE_S = 20

    def __init__(self, engine: str):
        self.engine = engine                      # the name the log lines carry
        self._cond = threading.Condition()
        self.active = False                       # a background request holds the slot (acquire → release)
        self.abort = False                        # an interactive request wants the slot: the background one stands down
        self.sock: socket.socket | None = None    # the background request's socket, once connected
        self.interactive_active = 0               # interactive requests (and pre-heats) in flight or waiting for the slot
        self.interactive_last_end = 0.0           # monotonic; the idle clock a background request waits on
        self._thread = threading.local()          # .wait_s: how long this thread's last background request queued

    def reset(self):
        """Back to the fresh state (tests)."""
        self.active = self.abort = False
        self.sock = None
        self.interactive_active, self.interactive_last_end = 0, 0.0

    # --- the interactive side -------------------------------------------------------------

    def interactive_begin(self):
        """An interactive request is about to go to the engine: evict the background request
        holding the slot, if any, and wait for it to unwind."""
        with self._cond:
            self.interactive_active += 1
            if not self.active:
                return
            self.abort = True
            if self.sock is not None:
                info(f"{self.engine}: interactive request arriving — aborting the background image request.")
                self.shutdown_socket(self.sock)
            else:
                trace(f"{self.engine}: interactive request arriving — the background request stands down before sending.")
            self._cond.notify_all()
            if not self._cond.wait_for(lambda: not self.active, timeout=self.PREEMPT_UNWIND_TIMEOUT_S):
                info(f"{self.engine}: background request did not unwind within {self.PREEMPT_UNWIND_TIMEOUT_S:.0f}s — "
                     "proceeding; it queues behind us in the engine.")

    def interactive_end(self, resets_idle: bool = True):
        with self._cond:
            self.interactive_active = max(0, self.interactive_active - 1)
            if resets_idle:
                self.interactive_last_end = time.monotonic()
            self._cond.notify_all()

    # --- the background side --------------------------------------------------------------

    def wait_seconds(self) -> float:
        """How long the calling thread's last background request queued for the slot."""
        return getattr(self._thread, "wait_s", 0.0)

    def acquire(self, idle_s: float):
        """Block until the slot is free and the engine has been idle for `idle_s` seconds."""
        self._thread.wait_s = 0.0
        with self._cond:
            waited = None
            while True:
                if self.interactive_active:
                    remaining, why = None, "an interactive request is in flight"
                elif self.active:
                    remaining, why = None, "another background request holds it"
                else:
                    remaining = idle_s - (time.monotonic() - self.interactive_last_end)
                    if remaining <= 0:
                        break
                    why = f"it must idle {remaining:.0f}s more after the last interactive request"
                if waited is None:
                    waited = time.monotonic()
                    trace(f"{self.engine}: background request waiting for the slot: {why}.")
                self._cond.wait(remaining)
            self.active = True
            self.abort = False
            self.sock = None
        if waited is not None:
            self._thread.wait_s = time.monotonic() - waited
            trace(f"{self.engine}: background request got the slot after {self._thread.wait_s:.1f}s")

    def release(self):
        with self._cond:
            self.active = False
            self.sock = None
            self._cond.notify_all()

    def register_connection(self, conn: http.client.HTTPConnection):
        """Put the background request's open connection under the gate, so an interactive
        arrival can shut it even while the request still waits for the engine's first byte (an
        image prefill happens before any header is sent). The socket is kept, not the connection:
        a `Connection: close` response (ds4-server's) makes getresponse() drop `conn.sock` and
        hand the socket to the response, and a gate holding the connection then shut nothing
        (2026-10-22: a photo ran its 60 s out ahead of the user's turn). Closes the connection
        and raises LLMPreempted when an interactive request already asked for the slot."""
        with self._cond:
            if self.abort:
                conn.close()
                raise LLMPreempted("interactive request arrived before the background request was sent")
            self.sock = conn.sock

    def preempted(self, chunks: int) -> LLMPreempted | None:
        """After a background request failed: the LLMPreempted to raise instead when the
        failure is our own eviction, None when it is a real failure."""
        if not self.abort:
            return None
        info(f"{self.engine}: background image request aborted for an interactive one after {chunks} chunk(s).")
        return LLMPreempted("interactive request took the slot")

    @staticmethod
    def shutdown_socket(sock: socket.socket):
        """Wake the thread blocked reading this socket: shutdown() interrupts a recv on every
        platform, where close() alone does not. The engine sees the FIN and cancels the request."""
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    @staticmethod
    def open_connection(base_url: str) -> http.client.HTTPConnection:
        """A fresh connection to `base_url`'s host, connected, with no timeout: the gate ends
        a request, not a clock."""
        from urllib.parse import urlparse
        url = urlparse(base_url)
        cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        conn = cls(url.hostname, url.port, timeout=None)
        conn.connect()
        return conn
