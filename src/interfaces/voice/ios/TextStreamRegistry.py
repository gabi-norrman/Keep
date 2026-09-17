import queue
import threading
from typing import Optional

from common.logging import trace, fine


class TextStreamRegistry:
    """Bridges the LLM token stream from a worker thread to the async /stream_text endpoint.

    Each in-flight request is keyed by a client-supplied request_id and gets a thread-safe
    queue that tokens are published to and the endpoint drains.
    """

    _instance: Optional['TextStreamRegistry'] = None

    MAX_QUEUED_EVENTS = 1000
    ORPHAN_CLEANUP_SECONDS = 60.0

    @classmethod
    def get_instance(cls) -> 'TextStreamRegistry':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        if cls._instance is not None:
            cls._instance.cancel_orphan_timers()
        cls._instance = None

    def __init__(self):
        self._queues: dict[str, queue.Queue] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def get_or_create(self, request_id: str) -> queue.Queue:
        with self._lock:
            q = self._queues.get(request_id)
            if q is None:
                q = queue.Queue(maxsize=self.MAX_QUEUED_EVENTS)
                self._queues[request_id] = q
                fine(f"Text stream queue created for request {request_id}")
            return q

    def publish(self, request_id: Optional[str], event: dict):
        if not request_id:
            return
        q = self.get_or_create(request_id)
        try:
            q.put_nowait(event)
        except queue.Full:
            trace(f"Text stream queue full for request {request_id}, dropping event")

    def close(self, request_id: Optional[str]):
        """Signal end-of-stream. A consumer draining the queue stops on the sentinel
        and discards the entry; if nobody ever connected, a timer cleans it up."""
        if not request_id:
            return
        q = self.get_or_create(request_id)
        try:
            q.put_nowait(None)
        except queue.Full:
            pass
        trace(f"Text stream closed for request {request_id}")
        timer = threading.Timer(self.ORPHAN_CLEANUP_SECONDS, self.discard, args=(request_id,))
        timer.daemon = True
        with self._lock:
            self._timers[request_id] = timer
        timer.start()

    def discard(self, request_id: Optional[str]):
        if not request_id:
            return
        with self._lock:
            timer = self._timers.pop(request_id, None)
            if self._queues.pop(request_id, None) is not None:
                trace(f"Text stream queue discarded for request {request_id}")
        if timer is not None:
            timer.cancel()

    def cancel_orphan_timers(self):
        """Cancel every pending orphan-cleanup timer (the entries stay); for shutdown and tests."""
        with self._lock:
            timers, self._timers = list(self._timers.values()), {}
        for timer in timers:
            timer.cancel()
