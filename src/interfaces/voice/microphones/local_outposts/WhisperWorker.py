"""Shared Whisper (MLX) transcription worker for local outposts.

One worker serves every mic. mlx_whisper.transcribe is stateless per call
(condition_on_previous_text=False) and the MLX model is cached internally keyed by
repo, so a single instance can transcribe all mics — and since one Metal GPU can't
truly run two decodes at once, jobs run on a single thread in FIFO order.

A *speculative* job transcribes the utterance-so-far during the trailing-silence
wait; the matching *final* then reuses that result instead of re-decoding,
overlapping the ~0.7s decode with the silence confirmation. A cached speculative
result is delivered synchronously in submit_final — never queued, because with
several mics the worker is usually mid-decode on another mic's fresh final at
that moment, and no queue priority can preempt a running decode. A still-running
speculative delivers its final the moment the decode completes (see _run_spec).

Ported from Tools/LocalSTTTests/whisper_turbo_streaming_test.py.
"""

import queue
import threading
import time
from pathlib import Path

import numpy as np

from .constants import (
    SAMPLE_RATE, MIN_SPEECH, DECODE_KWARGS, INITIAL_PROMPT, KEEPWARM_IDLE_SECONDS,
    RISK_CONFIDENCE_MIN_PCT, RISK_NO_SPEECH_MAX, RISK_COMPRESSION_MAX, RISK_TEMPERATURE_MAX,
    WAKE_WORD_HALLUCINATION_NO_SPEECH_MIN, WAKE_WORD_HALLUCINATION_CONFIDENCE_MAX_PCT,
)
from common.logging import info, error, fine


def normalize_loudness(audio: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    """Peak-normalize a segment so its loudest sample sits at `target_peak`.

    The mic outposts capture at wildly varying levels (measured peaks 0.07–1.0 across
    the STT test set), and Whisper decodes quiet/soft speech far worse than a clean,
    full-scale signal — a command murmured at peak 0.1 is mis-heard where the same words
    spoken loudly transcribe fine. Scaling every segment to a consistent ~full-scale level
    before decoding cost nothing and lifted English exact-match pass 79%→82% in the STT
    harness (Tools/STTTest), with no regressions. Silent/near-silent buffers (peak below
    the guard) are left untouched so we never blow up pure noise into a phantom signal."""
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak < 1e-4:
        return audio
    return (audio * (target_peak / peak)).astype(np.float32)


def model_cached(repo: str) -> bool:
    """Whether `repo` loads without a download: a local model directory, or a complete
    snapshot in the Hugging Face hub cache (~/.cache/huggingface/hub, outside the repo, so
    `//wipe` leaves it). mlx_whisper calls snapshot_download itself on the first transcribe,
    which on a fresh machine pulls ~3 GB with nothing but a log line to show for it — the
    caller announces that to the household."""
    if Path(repo).exists():
        return True
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    try:
        snapshot_download(repo_id=repo, local_files_only=True)
        return True
    except LocalEntryNotFoundError:
        info(f"Whisper model '{repo}' is not in the Hugging Face cache, the warm-up downloads it")
        return False


def model_download_size(repo: str):
    """Total size in bytes of the files a download of `repo` fetches, or None when the hub
    cannot be asked (offline, private repo) — the announcement then omits the size."""
    from huggingface_hub import HfApi
    try:
        siblings = HfApi().model_info(repo, files_metadata=True).siblings or []
        return sum(f.size or 0 for f in siblings) or None
    except Exception as e:
        info(f"Whisper model size lookup for '{repo}' failed ({e}), announcing without it")
        return None


def transcribe(audio: np.ndarray, repo: str, language, initial_prompt: str, allowed=None):
    """Decode one audio segment. Imports mlx_whisper lazily so the rest of the
    server still loads on platforms without MLX (Apple Silicon only).

    `language` is the forced Whisper language code (e.g. 'sv'), or None to
    auto-detect — Whisper then picks the language from the audio and reports it
    back as result['language'], which we return so the caller can route the
    reply/voice to that language.

    `allowed` is the configured language set (e.g. {'en', 'sv'}). When auto-detect
    lands outside it — Whisper occasionally returns a spurious language (e.g. 'ar')
    for a short clip even when the text is fine — we re-pick the best language WITHIN
    the set rather than letting the caller drop a perfectly good command. See
    _apply_restricted_detection."""
    import mlx_whisper
    audio = normalize_loudness(audio)
    t0 = time.time()
    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=repo,
        language=language,
        initial_prompt=initial_prompt,
        **DECODE_KWARGS,
    )
    elapsed = time.time() - t0
    text = (result.get("text") or "").strip()
    detected_language = _apply_restricted_detection(audio, repo, result.get("language"), allowed)
    audio_secs = len(audio) / SAMPLE_RATE
    rtf = elapsed / audio_secs if audio_secs else 0.0
    return text, detected_language, elapsed, audio_secs, rtf, segment_metrics(result)


def _apply_restricted_detection(audio, repo, detected, allowed):
    """Keep `detected` when it's already one of the `allowed` languages (the common
    case — zero extra cost). Otherwise the open-set auto-detect misfired (it ranges over
    all ~99 languages and is flaky on short audio), so fall back to a restricted detection
    pass that can only choose within `allowed` — keeping a real en/sv command instead of
    dropping it over a bogus label. fine()'d, not note()'d: with always-on multi-language
    auto-detect this misfires on plenty of background speech (no wake word), so it's noise
    at INFO level despite each occurrence being a full extra encoder pass (~0.5s)."""
    if not allowed or detected in allowed:
        return detected
    repicked = _detect_restricted(audio, repo, allowed)
    fine(f"Whisper auto-detected '{detected}' (outside configured {sorted(allowed)}) — ran a "
         f"restricted detection pass, re-picked '{repicked}'.")
    return repicked


def _detect_restricted(audio, repo, allowed):
    """Most probable language WITHIN `allowed`, read straight from the audio. Costs one
    extra encoder pass, so it's only called on the rare out-of-set miss (see
    _apply_restricted_detection). Reuses the model mlx_whisper.transcribe already cached
    (ModelHolder, same float16 dtype) so it doesn't load a second copy."""
    import mlx.core as mx
    from mlx_whisper.transcribe import ModelHolder
    from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim, N_SAMPLES, N_FRAMES
    model = ModelHolder.get_model(repo, mx.float16)
    mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
    mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
    _, probs = model.detect_language(mel)   # dict {lang_code: prob} for a single clip
    return max(allowed, key=lambda code: probs.get(code, 0.0))


def segment_metrics(result):
    """Confidence / hallucination-risk signals aggregated over the result segments.

    avg_logprob → conf (exp of mean per-token logprob, ~0..1; low = unsure).
    no_speech_prob → odds the clip is actually silence/noise (high = likely junk).
    compression_ratio → repetition detector (high = "the the the" hallucination loop).
    temperature → which fallback temp decoded it (>0 means greedy failed — trouble).
    Risk values use the worst (max) segment; logprob uses the mean.
    """
    segs = result.get("segments") or []
    if not segs:
        return None
    logprob = sum(s.get("avg_logprob", 0.0) for s in segs) / len(segs)
    return {
        "conf": float(np.exp(logprob)),
        "logprob": logprob,
        "no_speech": max(s.get("no_speech_prob", 0.0) for s in segs),
        "compression": max(s.get("compression_ratio", 0.0) for s in segs),
        "temperature": max(s.get("temperature", 0.0) for s in segs),
    }


def metrics_risky(m, conf_min_pct=RISK_CONFIDENCE_MIN_PCT):
    """True if any signal is past Whisper's standard risk thresholds. Risky
    transcripts are dropped, never sent to the server. `conf_min_pct` can be
    overridden per-clip (e.g. for wake-word clips, which score low on context
    alone); it currently matches the standard floor."""
    return bool(m) and (m["conf"] * 100 < conf_min_pct
                        or m["no_speech"] > RISK_NO_SPEECH_MAX
                        or m["compression"] > RISK_COMPRESSION_MAX
                        or m["temperature"] > RISK_TEMPERATURE_MAX)


def wake_word_hallucination_risky(m):
    """True for the correlated cross-engine hallucination signature: a wake-word fire that
    is simultaneously low-confidence AND breathy (high no_speech). The conjunction is the
    point — each signal alone overlaps genuine short commands, but only a forced-English
    hallucination of foreign/noise audio trips both at once. See the WAKE_WORD_HALLUCINATION_*
    constants. Apply only to a clip a wake word opened; never to command/continuation text."""
    return bool(m) and (
        m["no_speech"] > WAKE_WORD_HALLUCINATION_NO_SPEECH_MIN
        and m["conf"] * 100 < WAKE_WORD_HALLUCINATION_CONFIDENCE_MAX_PCT)


def format_metrics(m, conf_min_pct=RISK_CONFIDENCE_MIN_PCT):
    """One-line breakdown of every risk signal with its threshold and a ⚠ on any
    value that tripped. For debugging why a transcript was / wasn't flagged risky."""
    if not m:
        return "no metrics (no segments)"

    def field(label, value, tripped, rule):
        return f"{label}={value} (risky {rule}){' ⚠' if tripped else ''}"

    conf_pct = m["conf"] * 100
    return " · ".join([
        field("conf", f"{conf_pct:.0f}%", conf_pct < conf_min_pct, f"<{conf_min_pct:.0f}%"),
        field("no_speech", f"{m['no_speech']:.2f}", m["no_speech"] > RISK_NO_SPEECH_MAX, f">{RISK_NO_SPEECH_MAX}"),
        field("compression", f"{m['compression']:.2f}", m["compression"] > RISK_COMPRESSION_MAX, f">{RISK_COMPRESSION_MAX}"),
        field("temperature", f"{m['temperature']:.2f}", m["temperature"] > RISK_TEMPERATURE_MAX, f">{RISK_TEMPERATURE_MAX}"),
    ])


class _Job:
    __slots__ = ("kind", "audio", "spec_id", "on_result", "label", "submitted_at", "delivered")

    def __init__(self, kind, audio, spec_id=None, on_result=None, label=""):
        self.kind, self.audio = kind, audio
        self.spec_id, self.on_result, self.label = spec_id, on_result, label
        self.submitted_at = time.time()
        # Set when a completing speculative delivered this final's result directly
        # (see _run_spec) — the queued job then pops as a no-op.
        self.delivered = False


# Job priorities (lower number = dequeued first). Finals are the real work and must
# jump ahead of any pending speculative — a speculative only exists to fill GPU time
# that would otherwise be idle during one mic's silence wait. With multiple mics the
# GPU isn't idle, so a speculative must yield to another mic's final, otherwise the
# first final waits behind a second mic's speculative (turning 1 decode into 2).
PRIORITY_STOP = -2     # shut down promptly
PRIORITY_FINAL = 0
PRIORITY_SPEC = 1
PRIORITY_KEEPWARM = 2  # GPU keep-warm decode — pure filler; every real job jumps ahead


class WhisperWorker(threading.Thread):
    """Single shared GPU thread that transcribes every outpost's utterances with Whisper.

    Outposts submit speculative + final jobs tagged with a result callback; the worker
    invokes the callback with
    (text, metrics, audio, language, timing) for each completed final job (audio is the
    full final segment, for speaker ID; language is the Whisper code, detected when the
    worker runs in auto-detect mode; timing carries the decode's submit/start/done
    stamps for the STT latency breakdown). Finals are prioritized over pending
    speculatives; a final whose speculative result is already cached is delivered
    synchronously at submit (see submit_final), and one whose speculative is still
    running is delivered directly at that decode's completion (see _run_spec).

    `language` is a forced Whisper code (e.g. 'en') for a single-language setup, or
    None to auto-detect per utterance when several languages are configured.
    """

    STOP = object()

    def __init__(self, repo, language, allowed_languages=None,
                 initial_prompt=INITIAL_PROMPT, min_speech=MIN_SPEECH):
        super().__init__(daemon=True, name="LocalOutpostWhisper")
        self.repo, self.language, self.prompt = repo, language, initial_prompt
        # The configured language subtags (e.g. {'en', 'sv'}). When auto-detecting
        # (language is None) this bounds the result: an out-of-set detection triggers a
        # restricted re-pick rather than dropping the command. See transcribe().
        self.allowed_languages = allowed_languages
        self.min_speech = min_speech
        self._jobs: "queue.PriorityQueue" = queue.PriorityQueue()
        self._spec_results = {}     # spec_id -> (text, language, metrics, elapsed, rtf,
                                    #             submitted_at, started_at, done_at, spec_audio)
        self._cancelled = set()     # spec_ids whose speech resumed / were superseded by a fresh final
        self._pending_finals = {}   # spec_id -> queued final _Job awaiting a still-running speculative
        self._spec_counter = 0
        self._spec_inflight = 0     # speculatives queued-or-running now; capped at 1 (see submit_speculative)
        self._seq = 0               # tiebreak for equal priorities (stable FIFO) + avoids comparing jobs
        self._last_decode_at = time.time()  # wall-clock of the last completed GPU decode
        self._keepwarm_pending = False      # a keep-warm job is queued-or-running (max one)
        self._keepwarm_total = 0
        self._lock = threading.Lock()

    def metrics(self):
        """Snapshot of the worker's queue depth and leak-watch counters for the resource
        monitor. _spec_results / _cancelled should hover near zero — a steady climb means
        speculative results are produced but never consumed (a leak that grows GIL/GC load)."""
        with self._lock:
            return {
                "jobs": self._jobs.qsize(),
                "spec_results": len(self._spec_results),
                "cancelled": len(self._cancelled),
                "pending_finals": len(self._pending_finals),
                "inflight": self._spec_inflight,
                "spec_total": self._spec_counter,
                "keepwarm_total": self._keepwarm_total,
            }

    def warm_up(self):
        """Load + cache the model with a 1s silence decode so the first real
        utterance isn't slowed by a cold start. Runs on the calling thread."""
        info(f"Loading Whisper '{self.repo}' on MLX/Metal…")
        t0 = time.time()
        transcribe(np.zeros(SAMPLE_RATE, np.float32), self.repo, self.language, self.prompt)
        self._last_decode_at = time.time()
        info(f"Whisper model ready ({time.time() - t0:.1f}s warm-up).")

    def _enqueue(self, priority, item):
        with self._lock:
            seq = self._seq
            self._seq += 1
        self._jobs.put((priority, seq, item))

    def submit_speculative(self, audio, label=""):
        """Queue a speculative decode of the utterance-so-far, UNLESS one is already in
        flight — concurrent speculatives are capped at one. Every mic fires a speculative
        on each intra-utterance pause; left unbounded they pile uninterruptible decodes
        onto the single GPU/GIL thread and starve the audio callbacks, dropping captured
        audio. One-at-a-time keeps the speculative an opportunistic filler, never a storm.
        Returns the spec_id, or None when skipped — the caller's final then decodes fresh."""
        with self._lock:
            if self._spec_inflight > 0:
                return None
            self._spec_inflight += 1
            self._spec_counter += 1
            spec_id = self._spec_counter
        self._enqueue(PRIORITY_SPEC, _Job("spec", audio, spec_id=spec_id, label=label))
        return spec_id

    def cancel(self, spec_id):
        with self._lock:
            self._cancelled.add(spec_id)
            self._spec_results.pop(spec_id, None)
            self._pending_finals.pop(spec_id, None)

    def submit_final(self, audio, on_result, spec_id=None, label=""):
        """Queue the utterance's final decode — or skip the queue entirely. When the
        speculative's result is already cached, deliver it synchronously on the calling
        thread: with several mics the worker is usually mid-decode on another mic's
        fresh final at this moment, and no queue priority can preempt a running decode,
        so a queued promotion would land ~a full infer late and lose the server's
        first-arrival election to that rival final (observed 2026-08-12: promoted
        speculatives delivered 1ms after the rival on every 3-mic query). Delivering
        here is cheap — the callback's slow work (HTTP send, recording save, Scribe)
        is all handed off to executors. When the speculative is still running, register
        as pending so _run_spec delivers the result the moment that decode completes."""
        job = _Job("final", audio, spec_id=spec_id, on_result=on_result, label=label)
        if spec_id is not None:
            with self._lock:
                res = self._spec_results.pop(spec_id, None)
                if res is None and spec_id not in self._cancelled:
                    self._pending_finals[spec_id] = job
            if res is not None:
                (text, language, metrics, elapsed, rtf,
                 submitted_at, started_at, done_at, spec_audio) = res
                fine(f"[{label}] final (promoted speculative): '{text}' "
                     f"(lang={language}, {elapsed*1000:.0f} ms infer, {rtf:.2f}x RTF)")
                try:
                    on_result(text, metrics, audio, language,
                              {"submitted_at": submitted_at, "started_at": started_at,
                               "done_at": done_at, "decode": "spec", "audio": spec_audio})
                except Exception as e:
                    error(f"Promoted-transcript delivery failed for [{label}]: {e}")
                return
        self._enqueue(PRIORITY_FINAL, job)

    def idle_seconds(self):
        """Seconds since the last completed GPU decode (real or keep-warm)."""
        return time.time() - self._last_decode_at

    def submit_keepwarm(self):
        """Queue a tiny silence decode to hold the GPU at its warm clock (see
        KEEPWARM_IDLE_SECONDS). Lowest priority — any real job jumps ahead of a
        queued keep-warm — and capped at one queued-or-running at a time. The
        caller (LocalOutpostManager) gates on decode-idle time and on no mic being
        mid-utterance. Returns False when one is already pending."""
        with self._lock:
            if self._keepwarm_pending:
                return False
            self._keepwarm_pending = True
        self._enqueue(PRIORITY_KEEPWARM, _Job("keepwarm", None, label="keepwarm"))
        return True

    def stop(self):
        self._enqueue(PRIORITY_STOP, self.STOP)

    def run(self):
        while True:
            _, _, job = self._jobs.get()
            if job is self.STOP:
                break
            try:
                if job.kind == "spec":
                    self._run_spec(job)
                elif job.kind == "final":
                    self._run_final(job)
                elif job.kind == "keepwarm":
                    self._run_keepwarm()
            except Exception as e:
                error(f"Whisper transcription failed for [{job.label}]: {e}")

    def _run_keepwarm(self):
        with self._lock:
            self._keepwarm_pending = False
        # A real decode ran while this was queued — the GPU is already warm.
        if self.idle_seconds() < KEEPWARM_IDLE_SECONDS:
            fine("GPU keep-warm skipped — a real decode just ran.")
            return
        _, _, elapsed, _, _, _ = transcribe(
            np.zeros(SAMPLE_RATE, np.float32), self.repo, self.language, self.prompt)
        self._last_decode_at = time.time()
        self._keepwarm_total += 1
        fine(f"GPU keep-warm decode: {elapsed * 1000:.0f} ms "
             f"(#{self._keepwarm_total} since start)")

    def _run_spec(self, job):
        try:
            with self._lock:
                if job.spec_id in self._cancelled:
                    self._cancelled.discard(job.spec_id)
                    return  # resumed before we even started — skip the decode
            started_at = time.time()
            text, language, elapsed, _, rtf, metrics = transcribe(
                job.audio, self.repo, self.language, self.prompt, allowed=self.allowed_languages)
            self._last_decode_at = time.time()
            pending = None
            with self._lock:
                if job.spec_id in self._cancelled:
                    self._cancelled.discard(job.spec_id)  # resumed mid-decode — drop result
                else:
                    pending = self._pending_finals.pop(job.spec_id, None)
                    if pending is None:
                        self._spec_results[job.spec_id] = (text, language, metrics, elapsed, rtf,
                                                           job.submitted_at, started_at, time.time(),
                                                           job.audio)
                    else:
                        pending.delivered = True
            if pending is not None:
                # The utterance's final was already queued waiting for this decode — deliver
                # NOW (still on the worker thread) instead of caching the result for the
                # queued job to collect; that job could sit behind another mic's redundant
                # full decode, delaying a ready transcript by that decode's length. The
                # pending job's audio is the complete utterance (this job's is the
                # utterance-so-far), which speaker ID and the debug WAV need.
                fine(f"[{pending.label}] final (promoted speculative on completion): '{text}' "
                     f"(lang={language}, {elapsed*1000:.0f} ms infer, {rtf:.2f}x RTF)")
                pending.on_result(text, metrics, pending.audio, language,
                                  {"submitted_at": job.submitted_at, "started_at": started_at,
                                   "done_at": time.time(), "decode": "spec", "audio": job.audio})
        finally:
            # Free the single speculative slot whichever way this job retired (decoded,
            # skipped, or raised), so the cap never wedges. max() guards direct unit-test
            # calls that never went through submit_speculative.
            with self._lock:
                self._spec_inflight = max(0, self._spec_inflight - 1)

    def _run_final(self, job):
        if job.delivered:
            fine(f"[{job.label}] final already delivered at speculative completion — nothing to do.")
            return
        # No cached speculative result to consider — submit_final delivers a cached one
        # synchronously and never queues it. So this job's speculative is either still
        # queued behind it (lower priority) or already cancelled: make sure it's skipped
        # when it pops, and decode now.
        if job.spec_id is not None:
            self.cancel(job.spec_id)
        if len(job.audio) / SAMPLE_RATE < self.min_speech:
            fine(f"[{job.label}] dropping sub-{self.min_speech}s noise blip.")
            return
        started_at = time.time()
        text, language, elapsed, _, rtf, metrics = transcribe(
            job.audio, self.repo, self.language, self.prompt, allowed=self.allowed_languages)
        self._last_decode_at = time.time()
        fine(f"[{job.label}] final: '{text}' (lang={language}, {elapsed*1000:.0f} ms infer, {rtf:.2f}x RTF)")
        job.on_result(text, metrics, job.audio, language,
                      {"submitted_at": job.submitted_at, "started_at": started_at,
                       "done_at": time.time(), "decode": "final", "audio": job.audio})
