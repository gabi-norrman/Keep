import collections
import time
from typing import Optional

from common.logging import info, trace

_EMPTY_DEQUE: collections.deque = collections.deque()


class STTConfidenceMonitor:
    """Tracks per-microphone STT confidence history and 12h rolling averages for the DashboardTab.

    Confidence arrives already de-scaled onto a single 0..1 scale regardless of engine
    (Whisper vs Scribe — see SCRIBE_CONF_CALIBRATION), so values are directly comparable.
    Pure singleton, event-driven (no background thread): the request path calls record()
    for each accepted ("best") transcription; later partials of the same utterance only
    raise the existing bar.
    """

    _instance: Optional['STTConfidenceMonitor'] = None
    HISTORY_SIZE = 60
    ROLLING_WINDOW_SECONDS = 12 * 3600
    MAX_DISPLAY_MICS = 3

    @classmethod
    def get_instance(cls) -> 'STTConfidenceMonitor':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    def __init__(self):
        # mic name -> recent confidences (one bar per accepted utterance)
        self._data: dict[str, collections.deque] = {}
        # mic insertion order, so each mic keeps a stable graph slot
        self._mic_order: list[str] = []
        # mic name -> (timestamp, confidence), for the per-mic 12h rolling average.
        # The all-mics average is derived from these, so there's no parallel deque
        # to keep in sync when a partial updates an existing utterance's confidence.
        self._rolling_by_mic: dict[str, collections.deque] = {}
        # last utterance generation recorded per mic, to dedup growing partials
        self._last_generation: dict[str, int] = {}

    def record(self, mic: str, confidence: float, utterance_generation: int, now: Optional[float] = None):
        if not mic:
            trace("STTConfidenceMonitor.record called without a mic, ignoring.")
            return

        ts = time.time() if now is None else now

        if mic not in self._data:
            self._data[mic] = collections.deque(maxlen=self.HISTORY_SIZE)
            self._mic_order.append(mic)
            info(f"STTConfidenceMonitor tracking new mic '{mic}' (slot {len(self._mic_order)}).")
        mic_rolling = self._rolling_by_mic.setdefault(mic, collections.deque())

        if self._last_generation.get(mic) == utterance_generation:
            # A later partial of the same utterance — don't add a second bar. Keep the
            # highest confidence this mic reached, so the bar is order-independent.
            if confidence > self._data[mic][-1]:
                self._data[mic][-1] = confidence
                mic_rolling[-1] = (mic_rolling[-1][0], confidence)
                trace(f"Raised STT confidence to {confidence:.2f} for mic '{mic}' "
                      f"(utterance {utterance_generation}).")
            else:
                trace(f"STT confidence {confidence:.2f} for mic '{mic}' utterance "
                      f"{utterance_generation} not above current; ignoring later partial.")
            self._prune_rolling(ts)
            return

        self._last_generation[mic] = utterance_generation
        self._data[mic].append(confidence)
        mic_rolling.append((ts, confidence))
        self._prune_rolling(ts)
        trace(f"Recorded STT confidence {confidence:.2f} for mic '{mic}' (utterance {utterance_generation}).")

    @staticmethod
    def _prune_deque(rolling: collections.deque, cutoff: float):
        while rolling and rolling[0][0] < cutoff:
            rolling.popleft()

    def _prune_rolling(self, now: float):
        cutoff = now - self.ROLLING_WINDOW_SECONDS
        for mic_rolling in self._rolling_by_mic.values():
            self._prune_deque(mic_rolling, cutoff)

    @property
    def mics(self) -> list:
        """Mic names in first-seen order, capped at the number we can display."""
        return self._mic_order[:self.MAX_DISPLAY_MICS]

    def history(self, mic: str) -> collections.deque:
        return self._data.get(mic, _EMPTY_DEQUE)

    def rolling_avg_12h(self, mic: Optional[str] = None, now: Optional[float] = None) -> float:
        """12h rolling average confidence. Pass a mic for its own average, or omit for all mics."""
        ts = time.time() if now is None else now
        self._prune_rolling(ts)
        if mic is None:
            samples = [c for r in self._rolling_by_mic.values() for _, c in r]
        else:
            r = self._rolling_by_mic.get(mic)
            samples = [c for _, c in r] if r else []
        if not samples:
            return 0.0
        return sum(samples) / len(samples)
