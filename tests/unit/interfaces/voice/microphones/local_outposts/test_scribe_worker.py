"""Unit tests for the ElevenLabs Scribe v2 transcription worker.

The gating that decides *whether* Scribe runs lives in LocalOutpost and is covered by
test_local_outposts.py. These tests cover the other half: ScribeWorker.transcribe(), which
parses the cloud REST response into a ScribeResult, and ScribeWorker._run(), which dispatches
the result best-effort to the outpost callback. transcribe() is the shared unit the production
worker AND the STT accuracy harness both call, so its parsing is worth pinning down.
"""

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import interfaces.voice.microphones.local_outposts.ScribeWorker as mod
from interfaces.voice.microphones.local_outposts.ScribeWorker import transcribe, ScribeWorker, ScribeResult
from interfaces.voice.microphones.local_outposts.constants import SCRIBE_KEYTERMS, SAMPLE_RATE

AUDIO = np.zeros(SAMPLE_RATE, np.float32)  # 1s of silence — content is irrelevant, the POST is mocked


def fake_response(body):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = body
    return resp


def run_transcribe(body, audio=AUDIO, **kwargs):
    """Call transcribe() with requests.post mocked to return `body`; return (result, posted_data)."""
    with patch("requests.post", return_value=fake_response(body)) as post:
        result = transcribe(audio, SAMPLE_RATE, "key", **kwargs)
    return result, post.call_args.kwargs["data"]


class TestTranscribeConfidence:
    def test_confidence_is_geometric_mean_of_word_logprobs(self):
        """conf = exp(mean per-word logprob), so it lands on Whisper's 0..1 scale."""
        body = {"words": [{"type": "word", "logprob": -0.1},
                          {"type": "word", "logprob": -0.3}]}
        result, _ = run_transcribe(body)
        assert result.conf == pytest.approx(math.exp(-0.2))

    def test_confidence_skips_spacing_audio_events_and_missing_logprobs(self):
        """Only spoken 'word' tokens with a real logprob count — spacing/audio_event tokens and
        words without a logprob must not drag the average."""
        body = {"words": [
            {"type": "word", "logprob": -0.1},
            {"type": "spacing", "logprob": -9.0},       # not a word — ignored
            {"type": "audio_event"},                     # not a word, no logprob — ignored
            {"type": "word"},                            # word but logprob missing — ignored
            {"type": "word", "logprob": -0.3},
        ]}
        result, _ = run_transcribe(body)
        assert result.conf == pytest.approx(math.exp(-0.2))

    def test_confidence_zero_when_no_word_tokens(self):
        """An empty / non-word transcript yields conf 0.0, never a div-by-zero."""
        result, _ = run_transcribe({"words": [{"type": "audio_event"}]})
        assert result.conf == 0.0
        result, _ = run_transcribe({})
        assert result.conf == 0.0


class TestTranscribeBodyParsing:
    def test_text_is_stripped(self):
        result, _ = run_transcribe({"text": "  turn on the lights  "})
        assert result.text == "turn on the lights"

    def test_whitespace_or_missing_text_becomes_empty(self):
        assert run_transcribe({"text": "   "})[0].text == ""
        assert run_transcribe({})[0].text == ""

    def test_detected_language_and_probability_mapped(self):
        body = {"language_code": "swe", "language_probability": 0.97}
        result, _ = run_transcribe(body)
        assert result.language == "swe"
        assert result.language_probability == 0.97

    def test_missing_language_is_none_with_zero_probability(self):
        result, _ = run_transcribe({})
        assert result.language is None
        assert result.language_probability == 0.0

    def test_rtf_is_elapsed_over_audio_seconds(self):
        """rtf = elapsed / audio_seconds; a 0.5s clip transcribed in (mocked) real time still
        produces a finite ratio rather than dividing by zero."""
        half_second = np.zeros(SAMPLE_RATE // 2, np.float32)
        result, _ = run_transcribe({}, audio=half_second)
        assert result.rtf == pytest.approx(result.elapsed / 0.5)

    def test_empty_audio_does_not_divide_by_zero(self):
        result, _ = run_transcribe({}, audio=np.zeros(0, np.float32))
        assert result.rtf == 0.0


class TestTranscribeRequestShape:
    def test_posted_audio_is_peak_normalized_like_whispers(self):
        """Scribe must hear the same level Whisper does (2026-08-25 fix): a quiet capture
        (peak 0.1) is scaled to peak 0.95 before upload. Posting the raw mic signal cost
        Scribe the wake word on quiet genuine addresses and hallucinated 'Michelle' onto a
        Swedish clip, so this pins the normalization at the wire, not just in the helper."""
        import io
        import soundfile as sf
        quiet = np.zeros(SAMPLE_RATE, np.float32)
        quiet[100], quiet[200] = 0.1, -0.05
        with patch("requests.post", return_value=fake_response({})) as post:
            transcribe(quiet, SAMPLE_RATE, "key")
        _, buf, _ = post.call_args.kwargs["files"]["file"]
        posted, sr = sf.read(io.BytesIO(buf.getvalue()), dtype="float32")
        assert sr == SAMPLE_RATE
        assert float(np.abs(posted).max()) == pytest.approx(0.95, abs=1e-3)


    def test_language_hint_subtag_pins_language_code(self):
        """A locale hint ('en-US') is reduced to the bare subtag Scribe expects ('en')."""
        _, data = run_transcribe({}, language="en-US")
        assert data["language_code"] == "en"

    def test_no_language_hint_omits_language_code_for_autodetect(self):
        _, data = run_transcribe({}, language=None)
        assert "language_code" not in data

    def test_keyterms_default_to_scribe_keyterms(self):
        _, data = run_transcribe({})
        assert data["keyterms"] == list(SCRIBE_KEYTERMS)

    def test_empty_keyterms_disables_priming(self):
        """Passing [] explicitly turns priming off — the field must be absent, not an empty list."""
        _, data = run_transcribe({}, keyterms=[])
        assert "keyterms" not in data

    def test_decode_params_pinned_for_determinism(self):
        _, data = run_transcribe({})
        assert data["model_id"] == "scribe_v2"
        assert data["tag_audio_events"] == "false"
        assert data["temperature"] == "0"
        assert data["seed"] == "0"

    def test_http_error_propagates(self):
        """A REST failure raises out of transcribe() — the swallowing happens one layer up in
        _run, so callers that want the error (e.g. the harness) still see it here."""
        resp = MagicMock()
        resp.raise_for_status.side_effect = RuntimeError("502")
        with patch("requests.post", return_value=resp):
            with pytest.raises(RuntimeError):
                transcribe(AUDIO, SAMPLE_RATE, "key")


def make_worker():
    """Build a ScribeWorker without touching ConfigManager or spawning a thread pool."""
    w = object.__new__(ScribeWorker)
    w.api_key = "key"
    w.keyterms = SCRIBE_KEYTERMS
    w.model = "scribe_v2"
    w.timeout = 5
    return w


class TestWorkerDispatch:
    def test_success_invokes_callback_with_text_conf_language(self):
        w = make_worker()
        cb = MagicMock()
        result = ScribeResult("hi", 0.9, "en", 0.99, 0.1, 0.1)
        with patch.object(mod, "transcribe", return_value=result):
            w._run(AUDIO, "en-US", cb, "Mic")
        cb.assert_called_once_with("hi", 0.9, "en")

    def test_language_falls_back_to_hint_when_undetected(self):
        """When Scribe returns no language, the callback gets the outpost's language hint rather
        than None, so downstream locale routing still has something to work with."""
        w = make_worker()
        cb = MagicMock()
        result = ScribeResult("hi", 0.9, None, 0.0, 0.1, 0.1)
        with patch.object(mod, "transcribe", return_value=result):
            w._run(AUDIO, "sv-SE", cb, "Mic")
        cb.assert_called_once_with("hi", 0.9, "sv-SE")

    def test_transcription_failure_is_swallowed_no_callback(self):
        """A Scribe outage must never crash or block the local Whisper path: the exception is
        logged and swallowed, and the callback never fires."""
        w = make_worker()
        cb = MagicMock()
        with patch.object(mod, "transcribe", side_effect=RuntimeError("network down")):
            w._run(AUDIO, "en-US", cb, "Mic")  # must not raise
        cb.assert_not_called()
