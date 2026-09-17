import threading


class StderrLogger:
    """Redirects stderr into the log, coalescing rapid writes into one message.

    Python tracebacks emit caret indicators character by character; writes are buffered
    and flushed as a single info() line after 50ms of idle.
    """

    def __init__(self, original_stderr=None):
        self._buffer = []
        self._timer = None
        self._lock = threading.Lock()
        self._original_stderr = original_stderr

    def write(self, message):
        with self._lock:
            self._buffer.append(message)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(0.05, self._flush_buffer)
            self._timer.daemon = True
            self._timer.start()

    def _flush_buffer(self):
        with self._lock:
            if not self._buffer:
                return
            combined = "".join(self._buffer)
            self._buffer.clear()
            self._timer = None
        text = combined.strip()
        if text:
            from common.logging import info
            info(text)

    def flush(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._flush_buffer()
        if self._original_stderr:
            self._original_stderr.flush()
