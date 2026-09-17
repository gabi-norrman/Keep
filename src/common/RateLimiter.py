import time
from collections import deque
from typing import Optional


class RateLimiter:
    """Caps how many sends may happen within a sliding time window.

    Shared across send paths (iMessage, email, ...). `check()` returns None when a send
    is allowed or a short error string when the cap is exceeded; callers must call
    `record()` only after a successful send.
    """

    def __init__(self, max_events: int, window_seconds: float):
        self._max = max_events
        self._window = window_seconds
        self._timestamps: deque[float] = deque(maxlen=max_events)

    def check(self) -> Optional[str]:
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > self._window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._max:
            return f"rate limit: {self._max} sends per {int(self._window)}s"
        return None

    def record(self) -> None:
        self._timestamps.append(time.time())
