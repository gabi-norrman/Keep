"""ElevenLabs Scribe v2 transcription worker for local outposts.

Scribe runs in the cloud alongside the local Whisper decode on every utterance; its
transcript is posted to the server as an extra vote (see LocalOutpost._on_scribe_transcript).
Unlike the single-GPU WhisperWorker, Scribe is a stateless network call, so jobs run on a
small thread pool — several utterances / mics can be in flight at once.

The module-level transcribe() is the shared unit: the production ScribeWorker AND the STT
accuracy harness (Tools/STTTest/backends.py ScribeBackend) both call it, so the harness's
89.7% number validates the exact production path — the same arrangement WhisperBackend has
with WhisperWorker.transcribe.

The installed elevenlabs SDK (pinned for the TTS path) predates Scribe and has no
speech_to_text, so this POSTs the REST endpoint directly with requests. Auth is the same
api_keys.elevenlabs key the voice synthesizer already uses.
"""

import io
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .constants import SAMPLE_RATE, SCRIBE_MODEL, SCRIBE_TIMEOUT, SCRIBE_KEYTERMS
from .WhisperWorker import normalize_loudness
from common.logging import error, fine

ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"


@dataclass
class ScribeResult:
    text: str
    conf: float                  # exp(mean per-word logprob), 0..1 — same scale as Whisper's `conf`
    language: str | None         # detected language_code, or None
    language_probability: float
    elapsed: float               # request wall-time (s)
    rtf: float                   # real-time factor (elapsed / audio seconds)


def transcribe(audio, sample_rate, api_key, keyterms=None, language=None,
               model=SCRIBE_MODEL, timeout=SCRIBE_TIMEOUT) -> ScribeResult:
    """Transcribe one float32 segment with Scribe v2. requests/soundfile are imported
    lazily so importing this module stays cheap on hosts without them.

    `language` is an optional locale hint ('en-US') whose subtag pins Scribe's
    language_code; None lets Scribe auto-detect (it is one multilingual model, no
    per-language variant to choose). `keyterms` defaults to SCRIBE_KEYTERMS; pass [] to
    disable priming. tag_audio_events is off so non-speech tags never pollute a command;
    temperature/seed are pinned for determinism.

    Confidence is exp(mean per-word logprob) over the spoken words (skipping 'spacing' /
    'audio_event' tokens), matching Whisper's `conf` so the two compare on one 0..1 scale.
    """
    import requests
    import soundfile as sf

    sr = sample_rate or SAMPLE_RATE
    terms = SCRIBE_KEYTERMS if keyterms is None else keyterms
    lang = language.split("-")[0].lower() if language else None

    # Peak-normalize exactly as WhisperWorker.transcribe does, so both engines hear the
    # same level. Scribe used to get the raw mic signal; on WakeWordTest that cost it the
    # wake word on quiet genuine addresses ("Michelle" alone / "Show, pause the music")
    # and let it hallucinate "Michelle" onto a Swedish clip it decodes correctly as
    # Swedish once normalized. Reproduced in two back-to-back A/B runs (2026-08-25):
    # 4 FN / 4 FP -> 3 FN / 3 FP, Whisper side identical.
    audio = normalize_loudness(np.asarray(audio, dtype=np.float32))
    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    data = {"model_id": model, "tag_audio_events": "false",
            "temperature": "0", "seed": "0", "timestamps_granularity": "word"}
    if lang:
        data["language_code"] = lang
    if terms:
        data["keyterms"] = list(terms)  # array field — requests emits one part per item
    files = {"file": ("clip.wav", buf, "audio/wav")}

    t0 = time.time()
    resp = requests.post(ENDPOINT, headers={"xi-api-key": api_key},
                         data=data, files=files, timeout=timeout)
    elapsed = time.time() - t0
    resp.raise_for_status()
    body = resp.json()

    text = (body.get("text") or "").strip()
    detected = body.get("language_code") or None
    prob = body.get("language_probability", 0.0) or 0.0
    logprobs = [w.get("logprob", 0.0) for w in (body.get("words") or [])
                if w.get("type") == "word" and w.get("logprob") is not None]
    conf = float(np.exp(sum(logprobs) / len(logprobs))) if logprobs else 0.0
    audio_secs = len(audio) / sr
    rtf = elapsed / audio_secs if audio_secs else 0.0
    return ScribeResult(text=text, conf=conf, language=detected,
                        language_probability=prob, elapsed=elapsed, rtf=rtf)


class ScribeWorker:
    """Runs ElevenLabs Scribe transcriptions for the outposts on a small thread pool.

    submit() runs the cloud transcribe off-thread and invokes callback(text, conf, language)
    on success. Best-effort: any network/REST failure is logged and swallowed (no callback
    fires), so a Scribe outage or slow round-trip never blocks or crashes the local Whisper path.
    """

    def __init__(self, keyterms=None, model=SCRIBE_MODEL, timeout=SCRIBE_TIMEOUT, max_workers=3):
        from common.ConfigManager import config
        self.api_key = (config.get("api_keys", {}) or {}).get("elevenlabs")
        if not self.api_key:
            raise RuntimeError("ElevenLabs Scribe needs api_keys.elevenlabs in config.yaml "
                               "— none found.")
        self.keyterms = SCRIBE_KEYTERMS if keyterms is None else keyterms
        self.model = model
        self.timeout = timeout
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="LocalOutpostScribe")

    def submit(self, audio, language_hint, callback, label=""):
        self._executor.submit(self._run, audio, language_hint, callback, label)

    def _run(self, audio, language_hint, callback, label):
        try:
            r = transcribe(audio, SAMPLE_RATE, self.api_key, self.keyterms,
                           language=language_hint, model=self.model, timeout=self.timeout)
        except Exception as e:
            error(f"[{label}] Scribe transcription failed: {e}")
            return
        fine(f"[{label}] scribe: '{r.text}' (conf={r.conf:.1%}, lang={r.language}, "
             f"{r.elapsed * 1000:.0f} ms, {r.rtf:.2f}x RTF)")
        callback(r.text, r.conf, r.language or language_hint)

    def shutdown(self):
        self._executor.shutdown(wait=False)
