"""Fire-and-forget HTTP client local outposts use to talk to the in-process server.

Mirrors what the Android outpost sends: a GET /stream with the transcribed query
(wake word already stripped) and a GET /wake_word_detected on local wake detection.
Sends run on a small thread pool so a /stream call — which stays open while Floxy
speaks — never blocks the shared Whisper worker thread that triggers it.

Mirrors Android's voice identification too: before the /stream GET, the utterance
audio is run through the in-process SpeakerIdentifier (the equivalent of Android's
POST /identify_voice). That populates the server's identification history, so the
/stream handler's existing voting fills in the speaker. Identify and GET share one
ordered task, so the history is always populated before the server votes.
"""

import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .constants import SERVER_HOST, SEND_TIMEOUT, OUTPOST_SOURCE, OUTPOST_LOCATION, SAMPLE_RATE
from common.logging import info, trace, error, fine


class OutpostStreamClient:
    """Fire-and-forget HTTP client a local outpost uses to send transcriptions and signals to the server.

    Sends run on a small thread pool so a /stream call (open while Floxy speaks) never blocks
    the shared Whisper worker thread that triggers it.
    """

    def __init__(self, port: int, host: str = SERVER_HOST):
        self._base = f"http://{host}:{port}"
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="LocalOutpostSend")

    def build_stream_url(self, query: str, lang: str, confidence=None, wake_word=False, stt_ms=None, stt_wait_ms=None, stt_trigger=None, mic="", engine="", verified_phrase="", scribe_skipped=False, wake_word_confirmed=False) -> str:
        """Build the /stream URL. source=VoiceToVoice marks this as a spoken
        interaction (the server otherwise defaults non-app User-Agents to TextChat);
        low_stability is always false — Whisper transcriptions are treated as final.
        wake_word=True folds the wake-word reset into this same request (server runs
        the /wake_word_detected logic before voting), avoiding a separate out-of-order call.
        mic carries the originating outpost's device name so the server (and Home tab)
        can attribute the phrase to a specific microphone — distinct from source.
        engine names the STT backend (whisper/scribe) that produced this vote, so the
        Home tab can show which engine the winning transcription came from.
        verified_phrase (Scribe votes only) is the exact Whisper phrase this Scribe job
        ran on — the server matches it against the current utterance window to settle the
        wake-word verdict gate for the RIGHT utterance (Scribe's own transcript can
        legitimately differ from Whisper's, so it can't serve as the correlator).
        scribe_skipped=True tells the server no Scribe verdict is coming for this send
        (high-confidence skip, see SCRIBE_SKIP_CONFIDENCE_PCT), so the wake-word verdict
        gate must not hold the reply waiting for one.
        wake_word_confirmed=True (Scribe votes from a wake-word verify job only) tells the
        server this mic's Scribe heard the wake word, so another mic's veto on the same
        utterance is outweighed."""
        params = {
            "server_sound": "true",
            "source": OUTPOST_SOURCE,
            "lang": lang,
            "low_stability": "false",
            "query": query,
        }
        if OUTPOST_LOCATION:
            params["location"] = OUTPOST_LOCATION
        if wake_word:
            params["wake_word_detected"] = "1"
        if confidence is not None:
            params["confidence"] = f"{confidence:.4f}"
        if mic:
            params["mic"] = mic.strip()
        if engine:
            params["engine"] = engine
        if stt_ms is not None:
            params["stt_ms"] = str(stt_ms)
        if stt_wait_ms is not None:
            params["stt_wait_ms"] = str(stt_wait_ms)
        if stt_trigger:
            params["stt_trigger"] = stt_trigger  # "rms"/"vad": which gate started the winning decode
        if verified_phrase:
            params["verified_phrase"] = verified_phrase
        if scribe_skipped:
            params["scribe_skipped"] = "1"
        if wake_word_confirmed:
            params["wake_word_confirmed"] = "1"
        return f"{self._base}/stream?{urllib.parse.urlencode(params)}"

    def identify_async(self, audio, label=""):
        """Start speaker identification now and return its Future. Called at the first
        speculative pause so the ~40–80ms identify overlaps the trailing-silence wait
        (~350ms) instead of sitting on the critical path before the /stream GET. The
        returned future is handed to send_query so the GET waits on it (by then it's
        already done) — keeping the history-before-vote ordering while costing ~0ms."""
        return self._executor.submit(self._identify_speaker, audio, label)

    def send_query(self, query: str, lang: str, confidence=None, label="", wake_word=False, audio=None, stt_ms=None, stt_wait_ms=None, stt_trigger=None, identify_future=None, engine="", verified_phrase="", scribe_skipped=False, wake_word_confirmed=False):
        """Push a final transcription to /stream, optionally carrying the wake-word signal.

        `audio` is the float32 utterance segment; when present it's run through the
        in-process speaker identifier before the GET so the server can attribute the
        query to a speaker (mirrors Android's /identify_voice + /stream).

        `identify_future` is an identification already started earlier (at the first
        speculative pause, see identify_async). When given we wait on it instead of
        identifying inline, so the identify stays off the critical path. When absent
        (speculative never fired) we fall back to identifying inline before the GET.

        `engine` is the STT backend (whisper/scribe) behind this vote, forwarded to the
        server so the Home tab can attribute the phrase to an engine.

        `verified_phrase` (Scribe votes only) is the Whisper phrase this Scribe job ran
        on, used server-side to correlate the vote with its utterance (see
        build_stream_url). `wake_word_confirmed` marks a Scribe vote from a wake-word verify
        job (see build_stream_url)."""
        url = self.build_stream_url(query, lang, confidence, wake_word=wake_word, stt_ms=stt_ms, stt_wait_ms=stt_wait_ms, stt_trigger=stt_trigger, mic=label, engine=engine, verified_phrase=verified_phrase, scribe_skipped=scribe_skipped, wake_word_confirmed=wake_word_confirmed)
        conf_str = f"{confidence:.1%}" if confidence is not None else "n/a"
        info(f"  [{label}] Accepted phrase: '{query}' (lang={lang}, confidence={conf_str}, "
             f"wake_word={wake_word}, engine={engine or 'n/a'})")
        if identify_future is not None:
            self._executor.submit(self._wait_then_get, identify_future, url, label, wake_word)
        else:
            self._executor.submit(self._identify_then_get, url, audio, label, wake_word)

    def notify_wake_word(self, label=""):
        """Standalone wake-word signal — used only when the wake word arrives with no
        command after it (no query to fold it into)."""
        info(f"[{label}] wake word detected (no command) — notifying /wake_word_detected")
        self._executor.submit(self._get, f"{self._base}/wake_word_detected", label)

    def notify_speech_active(self, label=""):
        """Real-time 'the user is still talking' ping, fired at VAD speech-onset — long
        before this fragment's transcript is ready. Lets the server keep an in-flight
        incomplete request waiting (more_speech_is_incoming) through a mid-utterance pause
        instead of speaking a half-answer and muting the mic mid-sentence. Fire-and-forget."""
        self._executor.submit(self._get, f"{self._base}/speech_active", label)

    def send_false_positive(self, query: str, label=""):
        """Veto a Whisper wake-word detection that Scribe couldn't confirm: ask the server to
        kill the in-flight request running on `query`. The server matches on the normalized
        phrase, so a request that has already moved on to a different phrase is left untouched.
        Fire-and-forget GET to /false_positive, mirroring notify_wake_word/notify_speech_active."""
        params = {"query": query}
        if label:
            params["mic"] = label.strip()
        url = f"{self._base}/false_positive?{urllib.parse.urlencode(params)}"
        info(f"  [{label}] Scribe veto — requesting kill of false-positive request: '{query}'")
        self._executor.submit(self._get, url, label)

    def send_scribe_settled(self, query: str, label=""):
        """Tell the server Scribe finished verifying the Whisper wake-word fire for `query`
        without a vote or a veto (near-miss wake word, priming echo, low confidence...), so
        the reply's verdict gate can release now rather than wait out its cap.
        Fire-and-forget GET to /scribe_settled, mirroring send_false_positive."""
        params = {"query": query}
        if label:
            params["mic"] = label.strip()
        url = f"{self._base}/scribe_settled?{urllib.parse.urlencode(params)}"
        trace(f"  [{label}] Scribe no-vote — settling the verdict gate for '{query}'")
        self._executor.submit(self._get, url, label)

    def _identify_speaker(self, audio, label: str):
        """Compute the speaker for the utterance audio via the shared SpeakerIdentifier and
        log it, but do NOT update the server's identification history. Recording is deferred
        to the wake-word path (see _wait_then_get/_identify_then_get): this runs
        speculatively on ALL background speech, so attributing the identity here would let
        kitchen chatter overwrite the identified user with no one addressing Floxy.

        Returns (speaker, confidence), or None when identification couldn't run. Best-effort:
        a missing identifier, no enrolled users, or an unknown speaker just means the server
        falls back to no attribution. Imported lazily so this package stays light on
        platforms without the (heavy, Apple-Silicon) ML stack."""
        if audio is None or len(audio) == 0:
            return None
        try:
            from interfaces.voice.speaker_id.SpeakerIdentifier import SpeakerIdentifier
        except Exception as e:
            trace(f"[{label}] speaker identification unavailable: {e}")
            return None
        identifier = SpeakerIdentifier.get_instance()
        if identifier is None:
            trace(f"[{label}] speaker identification unavailable (not initialized).")
            return None
        try:
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            speaker, conf = identifier.identify(pcm, SAMPLE_RATE, 16, record=False)
            if speaker and speaker != "unknown":
                trace(f"[{label}] identified speaker '{speaker}' (conf {conf:.1%}).")
            else:
                # Speculative identification fires on every utterance (before any wake
                # word), so an unidentified speaker is the common no-op case — fine().
                fine(f"[{label}] speaker unknown (best '{speaker}' {conf:.1%}).")
            return speaker, conf
        except Exception as e:
            error(f"[{label}] speaker identification failed: {e}")
            return None

    def _record_identification(self, result, label: str):
        """Commit a deferred identification to the voting history once a wake word confirms
        the speaker is actually addressing Floxy. `result` is the (speaker, conf) tuple from
        _identify_speaker; None or 'unknown' is a no-op."""
        if not result:
            return
        speaker, conf = result
        try:
            from interfaces.voice.speaker_id.SpeakerIdentifier import SpeakerIdentifier
            identifier = SpeakerIdentifier.get_instance()
            if identifier is not None:
                identifier.record_identification(speaker, conf)
                if speaker and speaker != "unknown":
                    trace(f"[{label}] recorded identified user '{speaker}' (wake word).")
        except Exception as e:
            trace(f"[{label}] could not record identification: {e}")

    def _identify_then_get(self, url: str, audio, label: str, wake_word: bool = False):
        """One ordered task: identify, record the identity only on a wake word, then GET.
        Doing it here guarantees the history is set before the server votes. Used as the
        fallback when no speculative identification was started ahead of time."""
        result = self._identify_speaker(audio, label)
        if wake_word:
            self._record_identification(result, label)
        self._get(url, label)

    def _wait_then_get(self, identify_future, url: str, label: str, wake_word: bool = False):
        """Ordered task for the speculative path: wait for the pre-started identify to
        finish (almost always already done — it ran during the silence wait), record the
        identity only on a wake word, then GET. The wait preserves the history-before-vote
        ordering; a slow identify that blows the timeout just means the server falls back to
        no attribution, never a hang."""
        result = None
        try:
            result = identify_future.result(timeout=SEND_TIMEOUT)
        except Exception as e:
            trace(f"[{label}] pre-started identification did not finish before send: {e}")
        if wake_word:
            self._record_identification(result, label)
        self._get(url, label)

    def _get(self, url: str, label: str):
        try:
            with urllib.request.urlopen(url, timeout=SEND_TIMEOUT) as resp:
                resp.read()  # drain so the server isn't left mid-stream
        except Exception as e:
            error(f"[{label}] request to server failed ({url}): {e}")

    def shutdown(self):
        self._executor.shutdown(wait=False)
