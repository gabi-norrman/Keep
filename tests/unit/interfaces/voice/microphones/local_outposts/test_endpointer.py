"""Endpointer: the utterance segmentation state machine extracted from
LocalOutpost._endpoint_loop (2ef7fd8c). Both the live outposts and the offline STT
harness segment through this exact code, so a regression here silently changes where
every utterance starts and ends — these pin the STARTED/STOPPED contract from the
class docstring."""

import numpy as np
import pytest

from interfaces.voice.microphones.local_outposts.Endpointer import Endpointer, STARTED, STOPPED
from interfaces.voice.microphones.local_outposts.constants import (
    FRAME, FRAME_DURATION, MAX_SEGMENT, PRE_ROLL, SILENCE,
)

SILENCE_FRAMES = int(np.ceil(SILENCE / FRAME_DURATION))
PRE_ROLL_FRAMES = max(1, int(PRE_ROLL / FRAME_DURATION))


def frame(value=0.0):
    """One 100 ms frame; a distinct fill value tags it so audio() ordering is checkable."""
    return np.full(FRAME, value, np.float32)


def frame_values(audio):
    """The tag value of each FRAME-sized block in a concatenated audio() result."""
    return [float(audio[i]) for i in range(0, len(audio), FRAME)]


@pytest.mark.unit
class TestEndpointer:

    def test_quiet_frames_never_open_an_utterance(self):
        ep = Endpointer()
        for _ in range(PRE_ROLL_FRAMES * 3):
            assert ep.feed(frame(), loud=False) is None
        assert ep.in_speech is False
        assert ep.has_audio is False
        assert ep.audio().size == 0

    def test_first_loud_frame_starts_and_includes_pre_roll(self):
        ep = Endpointer()
        ep.feed(frame(1.0), loud=False)
        ep.feed(frame(2.0), loud=False)
        assert ep.feed(frame(3.0), loud=True) == STARTED
        assert ep.in_speech is True and ep.has_audio is True
        # The quiet lead-in must reach Whisper (speech onsets get clipped otherwise).
        assert frame_values(ep.audio()) == [1.0, 2.0, 3.0]

    def test_pre_roll_is_bounded_to_the_newest_frames(self):
        ep = Endpointer()
        total = PRE_ROLL_FRAMES + 5
        for i in range(total):
            ep.feed(frame(float(i)), loud=False)
        ep.feed(frame(99.0), loud=True)
        values = frame_values(ep.audio())
        # Only the newest pre-roll frames survive, oldest quiet frames dropped. The
        # trigger frame passes through the deque before the loud check, so it occupies
        # one of the PRE_ROLL_FRAMES slots itself.
        assert values == [float(i) for i in range(6, total)] + [99.0]
        assert len(values) == PRE_ROLL_FRAMES

    def test_stops_after_trailing_silence_and_keeps_the_tail(self):
        ep = Endpointer()
        ep.feed(frame(1.0), loud=True)
        for _ in range(SILENCE_FRAMES - 1):
            assert ep.feed(frame(), loud=False) is None
        assert ep.feed(frame(), loud=False) == STOPPED
        # Trailing silence is part of the captured utterance, not discarded.
        assert len(frame_values(ep.audio())) == 1 + SILENCE_FRAMES

    def test_loud_frame_resets_the_silence_run(self):
        ep = Endpointer()
        ep.feed(frame(), loud=True)
        for _ in range(SILENCE_FRAMES - 1):
            ep.feed(frame(), loud=False)
        assert ep.feed(frame(), loud=True) is None      # speech resumed
        assert ep.silence_run == 0.0
        for _ in range(SILENCE_FRAMES - 1):             # a fresh full run is needed again
            assert ep.feed(frame(), loud=False) is None

    def test_max_segment_forces_a_stop_during_continuous_speech(self):
        ep = Endpointer()
        max_frames = int(MAX_SEGMENT / FRAME_DURATION)
        result = None
        for _ in range(max_frames):
            result = ep.feed(frame(), loud=True)
        assert result == STOPPED
        assert ep.speech_secs == pytest.approx(MAX_SEGMENT)

    def test_feeds_after_stop_keep_returning_stopped_until_reset(self):
        ep = Endpointer()
        ep.feed(frame(), loud=True)
        for _ in range(SILENCE_FRAMES):
            ep.feed(frame(), loud=False)
        # A caller that hasn't read audio()/reset() yet keeps seeing STOPPED while the
        # silence continues. (A loud frame here would reset the silence run and reopen
        # the utterance — both callers reset immediately on STOPPED, so only the quiet
        # continuation is live behavior.)
        assert ep.feed(frame(), loud=False) == STOPPED
        assert ep.feed(frame(), loud=False) == STOPPED

    def test_reset_allows_a_fresh_utterance(self):
        ep = Endpointer()
        ep.feed(frame(1.0), loud=True)
        for _ in range(SILENCE_FRAMES):
            ep.feed(frame(), loud=False)
        ep.reset()
        assert ep.has_audio is False
        assert ep.silence_run == 0.0
        assert ep.feed(frame(2.0), loud=True) == STARTED
        assert frame_values(ep.audio()) == [2.0]

    def test_clear_pre_roll_drops_stale_lead_in(self):
        # After a mute, buffered frames of Floxy's own voice must not open (or prefix)
        # the next utterance.
        ep = Endpointer()
        ep.feed(frame(7.0), loud=False)
        ep.clear_pre_roll()
        ep.feed(frame(1.0), loud=True)
        assert frame_values(ep.audio()) == [1.0]
