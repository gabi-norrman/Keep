"""Voice activity detection backends for local outposts.

Both backends expose is_speech(frame) -> bool over the same 100 ms capture frames
so the endpoint state machine stays VAD-agnostic. Silero wants fixed 512-sample
sub-windows, so it buffers the incoming frames and reports speech if ANY contained
sub-window crosses the threshold. Each backend is stateful per stream, so every
outpost owns its own instance. Ported from the LocalSTTTests whisper script
(TEN VAD dropped).
"""

import collections

import numpy as np

from .constants import (SAMPLE_RATE, FRAME_DURATION, RMS_THRESHOLD_MULTIPLIER,
                        RMS_THRESHOLD_FLOOR, RMS_FLOOR_RAISE_SECONDS,
                        RMS_FLOOR_SETTLE_SECONDS, RMS_FLOOR_PERCENTILE)


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame * frame)) + 1e-9)


class RmsVAD:
    """Short-term energy gate with a room-adaptive threshold.

    Cheap, but a loud clatter reads as 'speech' just as much as a quiet word reads as
    silence. The threshold ADAPTS to the room with two percentile-robust rules (see
    RMS_FLOOR_PERCENTILE) over the recent frames:
    - RAISE (fast attack): if the last RMS_FLOOR_RAISE_SECONDS' LOW percentile sits
      above the floor (~90% of the window elevated; one spurious dip can't block
      it), the ambience rose — lift the floor to that percentile. Sustained noise
      (post-reply speaker hiss, a dishwasher) re-arms the gate within ~a second
      instead of pegging it 'loud'.
    - SETTLE: if the last RMS_FLOOR_SETTLE_SECONDS' HIGH percentile is
      sub-threshold (~90% of the window quiet; a few clicks can neither disarm the
      settle nor hold the floor up), re-anchor the floor to that percentile —
      hugging the quiet room's level from above and undoing any speech-inflated
      raise.
    Threshold = floor × RMS_THRESHOLD_MULTIPLIER, clamped at RMS_THRESHOLD_FLOOR.

    The tracker models THE ROOM WHEN NOBODY IS SPEAKING: frames the main VAD
    attributes to speech are excluded via `speech_hint` (the endpoint loop passes
    silero's per-frame verdict), so the raise rule can never chase the user's own
    voice. Without this, mid-utterance raises lifted the threshold above the
    speaker's quieter passages, half-deafening the gate and firing premature
    speculatives on speech it mistook for silence (observed 2026-08-12). The hint
    also excludes silero's offset-decay tail — residual voice energy, not ambience.

    The owning outpost seeds floor+threshold with a ~1s ambient calibration; the
    tracker maintains them from then on — but ONLY when fed continuously (every
    non-speech frame, in and out of utterances), which the endpoint loop does.
    """
    name = "rms"
    RAISE_FRAMES = max(1, round(RMS_FLOOR_RAISE_SECONDS / FRAME_DURATION))
    SETTLE_FRAMES = max(2, round(RMS_FLOOR_SETTLE_SECONDS / FRAME_DURATION))

    def __init__(self, threshold=None, label=""):
        self.threshold = threshold     # seeded by ambient calibration if None
        self.label = label
        self.last_score = 0.0
        self.noise_floor = None        # current ambient-floor estimate (RMS); None until known
        self.raise_min = None          # live min of the last 1s — the raise rule's input,
                                       # surfaced on the Stats tab as rise-pressure vs the floor
        self._recent = collections.deque(maxlen=self.SETTLE_FRAMES)

    def is_speech(self, frame, speech_hint=False):
        self.last_score = rms(frame)
        if not speech_hint:
            self._observe(self.last_score)
        return self.threshold is not None and self.last_score > self.threshold

    @staticmethod
    def _percentile(ordered, p):
        return ordered[max(0, min(len(ordered) - 1, round(p * (len(ordered) - 1))))]

    def _observe(self, score):
        self._recent.append(score)
        if len(self._recent) < self.RAISE_FRAMES:
            return
        floor = self.noise_floor
        raise_ref = self._percentile(sorted(list(self._recent)[-self.RAISE_FRAMES:]),
                                     1.0 - RMS_FLOOR_PERCENTILE)
        self.raise_min = raise_ref
        if floor is None or raise_ref > floor:
            floor = raise_ref
        if self.threshold is not None and len(self._recent) == self.SETTLE_FRAMES:
            settle_ref = self._percentile(sorted(self._recent), RMS_FLOOR_PERCENTILE)
            if settle_ref < self.threshold:
                floor = settle_ref
        if floor == self.noise_floor:
            return
        self.noise_floor = floor
        self.threshold = max(floor * RMS_THRESHOLD_MULTIPLIER, RMS_THRESHOLD_FLOOR)

    def reset(self):
        pass  # the ambient tracker deliberately survives utterance resets


class SileroVAD:
    """Neural voice activity detector built on Silero VAD (~2 MB), the de-facto Whisper front-end."""
    name = "silero"
    WINDOW = 512  # required input size at 16 kHz

    def __init__(self, threshold):
        import torch
        from silero_vad import load_silero_vad
        self._torch = torch
        self.model = load_silero_vad()
        self.threshold = threshold
        self.last_score = 0.0
        self._buf = np.empty(0, np.float32)

    def is_speech(self, frame):
        self._buf = np.concatenate([self._buf, frame])
        speech, scores = False, []
        # inference_mode is essential, not just an optimisation: Silero is a stateful
        # LSTM whose hidden state is carried across calls, so with autograd on, each
        # forward chains onto the previous one's graph and the graph grows unbounded —
        # ~25 KB per forward, never freed until reset_states(). On a silent night (no
        # utterances → no reset) that leaked ~5 GB/h across both mics, swamping the box
        # with GC/swap stalls that starved the capture callback. inference_mode builds
        # no graph at all, so memory stays flat however long the VAD runs without a reset.
        with self._torch.inference_mode():
            while len(self._buf) >= self.WINDOW:
                chunk, self._buf = self._buf[:self.WINDOW], self._buf[self.WINDOW:]
                prob = self.model(self._torch.from_numpy(chunk), SAMPLE_RATE).item()
                scores.append(prob)
                speech = speech or prob >= self.threshold
        if scores:
            self.last_score = max(scores)
        return speech

    def reset(self):
        self.model.reset_states()
        self._buf = np.empty(0, np.float32)


def make_vad(backend: str, threshold, label=""):
    """Build a fresh VAD instance. backend='silero' is strict: an import failure
    propagates so the outpost refuses to start (loudly, via the manager's start
    error) instead of silently running the RMS gate as the main VAD — a mode that
    is never wanted in production and has a known deafness bug there (its noise
    floor adapts to the user's own speech; without silero no hint exists to
    exclude it). Explicit backend='rms' still builds one for tests/dev."""
    if backend == "silero":
        try:
            return SileroVAD(threshold)
        except ImportError as e:
            raise RuntimeError(
                f"Silero VAD unavailable ({e}) — refusing to fall back to the RMS "
                f"gate as main VAD. Fix the torch/silero-vad install.") from e
    return RmsVAD(label=label)
