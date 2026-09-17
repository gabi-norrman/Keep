"""Frame-driven utterance segmentation: the pre-roll buffer, in-speech flag, captured
frames, and trailing-silence bookkeeping that decide where an utterance starts and ends.

Extracted from LocalOutpost._endpoint_loop so the live outpost and the offline STT
harness (Tools/STTTest/endpoint.py) segment audio with the SAME code — the harness can
never drift from what production actually feeds Whisper. The VAD verdict stays outside
(callers score each frame and pass `loud`): the live loop needs the raw score for
logging and the energy gate, and the harness drives its own per-clip VAD instance.
"""

import collections

import numpy as np

from .constants import FRAME_DURATION, MAX_SEGMENT, PRE_ROLL, SILENCE

STARTED = "started"   # this frame opened an utterance
STOPPED = "stopped"   # the utterance is complete — read audio(), then reset()


class Endpointer:
    def __init__(self):
        pre_roll_frames = max(1, int(PRE_ROLL / FRAME_DURATION))
        self._pre_buffer = collections.deque(maxlen=pre_roll_frames)
        self._speech = []
        self.in_speech = False
        self.silence_run = 0.0    # consecutive quiet seconds per the caller's `loud` verdicts

    @property
    def speech_secs(self) -> float:
        return len(self._speech) * FRAME_DURATION

    @property
    def has_audio(self) -> bool:
        """An utterance is in progress or captured frames are pending."""
        return self.in_speech or bool(self._speech)

    def audio(self) -> np.ndarray:
        return np.concatenate(self._speech) if self._speech else np.zeros(0, np.float32)

    def reset(self):
        self._speech = []
        self.in_speech = False
        self.silence_run = 0.0

    def clear_pre_roll(self):
        """Drop buffered pre-roll frames (e.g. after a mute, so stale audio of Floxy's
        own voice can't open the next utterance)."""
        self._pre_buffer.clear()

    def feed(self, frame: np.ndarray, loud: bool) -> str | None:
        """Advance one frame. Returns STARTED when this frame opens an utterance,
        STOPPED when the utterance is complete (SILENCE of trailing quiet, or
        MAX_SEGMENT of speech), else None. After STOPPED the caller reads audio()
        and calls reset(); further feeds before that keep returning STOPPED."""
        if not self.in_speech:
            self._pre_buffer.append(frame)
            if not loud:
                return None
            self.in_speech = True
            self._speech = list(self._pre_buffer)
            self._pre_buffer.clear()
            self.silence_run = 0.0
            return STARTED
        self._speech.append(frame)
        if loud:
            self.silence_run = 0.0
        else:
            self.silence_run += FRAME_DURATION
        if self.silence_run >= SILENCE or self.speech_secs >= MAX_SEGMENT:
            return STOPPED
        return None
