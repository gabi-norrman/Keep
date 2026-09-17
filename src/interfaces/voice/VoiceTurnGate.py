"""Reply-timing gate for spoken replies (extracted verbatim from RequestProcessor).

Holds the first sound of a reply until it is natural to speak: the user has stopped
talking (sentence completeness, VAD onset pings, growing transcripts) and, for a
Whisper wake-word fire, Scribe has had its chance to veto. One gate per request
handler; the launcher installs a factory on RequestProcessor when the voice interface
is enabled, and the microphone routes arm the per-request signals through it.
"""
import time
from typing import TYPE_CHECKING, Callable

from common.FloxyDatabase import FloxyDatabase
from common.logging import info, trace, note, fine
from assistant.AssistantEvents import observers
from interfaces.voice.microphones.speech_analysis.SentenceCompletenessDetector import SentenceCompletenessDetector
from interfaces.voice.microphones.local_outposts.constants import ENGINE_WHISPER, SCRIBE_VERIFY_WAKE_WORD
from interfaces.voice.microphones.local_outposts.scribe_status import scribe_is_running

if TYPE_CHECKING:
    from assistant.AssistantRequest import RequestContext
    from interfaces.voice.microphones.speech_analysis.SpeechAnalyzer import SpeechAnalyzer


class VoiceTurnGate:
    """Holds the first sound of a spoken reply until it is natural to speak.

    Waits for the user to stop talking (sentence completeness, VAD onset pings, growing
    transcripts) and, for a Whisper wake-word fire, gives Scribe its chance to veto. One
    gate per request handler, built by the voice interface's turn_gate_factory.
    """

    MINIMUM_SECONDS_TO_WAIT_BEFORE_SPEAKING = 0.6
    SECONDS_TO_WAIT_BEFORE_SPEAKING_IF_SENTENCE_INCOMPLETE = 2.0
    # Absolute cap on how long the "user is still speaking" signal (more_speech_is_incoming,
    # set by the content-aware length-growth check) can keep a reply waiting. Bounds a
    # wedged mic or a noisy room from holding a response hostage forever.
    MAX_TOTAL_WAIT_FOR_MORE_SPEECH = 5.0
    # How long a /speech_active VAD onset ping holds the reply on its own. The ping fires
    # at speech onset, before any transcript exists, so it can't tell a real continuation
    # from the same command re-detected by another mic (multi-mic echo, trailing audio).
    # It's therefore TENTATIVE: it only buys this grace — long enough for the continuation's
    # transcript to arrive and confirm via the length-growth check (which sets
    # more_speech_is_incoming and escalates to the full hold). If no longer transcript
    # materializes within the grace, we speak; phantom onsets can't hold a reply hostage.
    SPEECH_ONSET_GRACE = 1.5
    # How long a Whisper wake-word reply holds at the natural-timing gate waiting for Scribe's
    # verdict (a confirming vote or a veto) before it speaks. Scribe is the more accurate engine
    # and the only thing that can veto a Whisper wake-word false positive, but it runs a beat
    # behind Whisper — without this gate its verdict routinely lands after Floxy has already
    # started speaking the hallucinated reply. Scribe fires the instant Whisper accepts and runs
    # concurrently with the LLM+TTS (mean ~0.76s), so most of it has elapsed by the time we reach
    # the gate and the added wait is small. Bounded because Scribe can fail silently (network
    # error → no callback → no server signal): past this cap we speak on Whisper alone.
    MAX_WAIT_FOR_SCRIBE_VERDICT = 1.5

    def __init__(self, speech_analyzer: "SpeechAnalyzer", is_killed: Callable[[], bool],
                 kill_reason: Callable[[], str] | None = None):
        """is_killed reports whether the owning request was killed — read live inside the
        waits so a barge-in or Scribe veto aborts the hold at the same instant as before.
        kill_reason reports why (see RequestProcessor.kill), so an abort while holding for
        Scribe's verdict is logged as what it was: a veto, a superseding transcription, ..."""
        self.speech_analyzer = speech_analyzer
        self._is_killed = is_killed
        self._kill_reason = kill_reason or (lambda: "")
        self.more_speech_is_incoming = None  # CONFIRMED: timestamp the length-growth check saw a longer transcript
        self.speech_onset_at = None          # TENTATIVE: timestamp of the last /speech_active VAD onset ping
        # Snapshot at request start (see begin_turn): the utterance window this reply belongs
        # to and whether it opened with a wake word — together they decide if the natural-timing
        # gate waits for Scribe's verdict before speaking (see _wait_for_scribe_verdict).
        self._utterance_generation = -1
        self._is_wake_word_send = False
        # The outpost skipped Scribe for this send (high Whisper confidence) — no verdict
        # will ever arrive, so the gate must not hold for one.
        self._scribe_skipped = False

    @property
    def is_killed(self) -> bool:
        return self._is_killed()

    # --- arming, called by the microphone routes --------------------------------------
    def begin_turn(self, request_args: dict):
        """Snapshot the utterance window and wake-word flags when the request is dispatched.
        The wake word (handled in /stream before dispatch) already bumped the generation, so
        this captures the utterance window this reply is answering. Only a wake-word send can
        be a Whisper false positive Scribe would veto — a follow-up has no wake word to
        disconfirm — so record both to gate the Scribe wait in _wait_for_scribe_verdict."""
        args = request_args or {}
        self._utterance_generation = self.speech_analyzer.utterance_generation
        self._is_wake_word_send = args.get('wake_word_detected') in ('1', 'true')
        self._scribe_skipped = args.get('scribe_skipped') in ('1', 'true')

    def arm_speech_onset(self, now: float):
        """A VAD onset ping (/speech_active): TENTATIVE hold, see SPEECH_ONSET_GRACE."""
        self.speech_onset_at = now

    def confirm_more_speech(self, now: float):
        """A genuinely longer transcript arrived (/stream length-growth check): CONFIRMED hold."""
        self.more_speech_is_incoming = now

    # --- the gate ---------------------------------------------------------------------
    def _more_speech_wait(self) -> tuple[bool, float, float]:
        """How long, if at all, to keep holding the reply for the user to finish speaking.

        Returns (active, elapsed, target): whether a "more speech" signal is live, seconds
        since it fired, and how long it justifies waiting. Two deliberately asymmetric
        signals feed this:
          • more_speech_is_incoming — CONFIRMED. Set only by the content-aware length-growth
            check in /stream when a genuinely longer transcript arrived. Justifies the full
            hold (SECONDS_TO_WAIT_BEFORE_SPEAKING_IF_SENTENCE_INCOMPLETE), looping until the
            phrase stops growing or the absolute cap.
          • speech_onset_at — TENTATIVE. Armed by a /speech_active VAD onset ping, which
            fires before any transcript exists and so can't tell a real continuation from
            the same command re-detected by another mic. It only buys SPEECH_ONSET_GRACE —
            long enough for the continuation's transcript to arrive and upgrade us to the
            confirmed signal. On its own it can never escalate, so phantom onsets (multi-mic
            echo, room noise) can't hold a reply hostage.
        The confirmed signal always wins when both are present."""
        now = time.time()
        if self.more_speech_is_incoming is not None:
            return True, now - self.more_speech_is_incoming, VoiceTurnGate.SECONDS_TO_WAIT_BEFORE_SPEAKING_IF_SENTENCE_INCOMPLETE
        if self.speech_onset_at is not None:
            elapsed = now - self.speech_onset_at
            if elapsed < VoiceTurnGate.SPEECH_ONSET_GRACE:
                return True, elapsed, VoiceTurnGate.SPEECH_ONSET_GRACE
        return False, 0.0, 0.0

    # Runs on: request thread, from RequestProcessor._handle_first_audio_chunk — i.e. right
    # before the first sound of the reply, never on a separate thread hop.
    def wait(self, ctx: "RequestContext | None", reply_text: str, time_to_first_token: float,
             chunk_time: float) -> bool:
        """Block until it is natural to start speaking. Returns False if the request was
        killed while holding (the caller aborts the reply), True otherwise."""
        target_time = VoiceTurnGate.SECONDS_TO_WAIT_BEFORE_SPEAKING_IF_SENTENCE_INCOMPLETE
        # reply_text is the formatted message sent to the LLM ("Alex (VoiceToVoice in
        # Kitchen): turn off the lights") — it carries a name/source/location header. That
        # header must stay for the is_query_common lookup (the DB stores the same formatted
        # string), but it poisons the completeness model: the spaCy parse, first/last token,
        # length buckets and one-word check all see the header instead of the actual utterance.
        # Feed is_complete() the bare utterance so it judges what the user really said.
        raw_utterance = (ctx.user_input if ctx else None) or reply_text
        engine = ctx.engine if ctx else None
        if engine:
            # Local-outpost STT (whisper/scribe) endpoints on silence and sends a final,
            # already-complete segment (low_stability=false) — never a growing transcript
            # like the phones. The completeness model only ever judges what arrived, so on
            # an outpost phrase it can do nothing but add a needless hold. Treat every
            # outpost utterance as complete and skip the model entirely.
            info(f"Outpost STT ({engine}) — treating phrase as complete, lets go!")
            target_time = VoiceTurnGate.MINIMUM_SECONDS_TO_WAIT_BEFORE_SPEAKING
        elif len(reply_text.split()) > 1:
            if FloxyDatabase.get_instance().is_query_common(reply_text):
                info("Common query, lets go!")
                target_time = VoiceTurnGate.MINIMUM_SECONDS_TO_WAIT_BEFORE_SPEAKING
            elif SentenceCompletenessDetector.is_complete(raw_utterance):
                info("Sentence complete, lets go!")
                target_time = VoiceTurnGate.MINIMUM_SECONDS_TO_WAIT_BEFORE_SPEAKING
        else:
            trace("One word query or more speech incoming, lets wait for the user.")

        more_speech, total_time, _ = self._more_speech_wait()
        if more_speech:
            if target_time < 1:
                trace("Upping target time a bit - user is still speaking.")
                target_time = 1
        else:
            # This pause exists so we don't start talking right on top of the user, so the
            # clock must run from the moment they STOPPED SPEAKING — not from when the reply
            # became ready. Outposts report that moment (via stt_ms → speech_end_epoch);
            # by reply time the STT endpointing (~0.4s silence) + decode have usually already
            # consumed the whole target, so this normally computes to no sleep at all. Clients
            # without it (phones) fall back to server-side latency as a rough proxy.
            speech_end = ctx.speech_end_epoch if ctx else None
            if speech_end is not None:
                total_time = time.time() - speech_end
                fine(f"Anchoring natural-timing wait to speech end: {total_time:.2f}s already elapsed.")
            else:
                total_time = time_to_first_token + chunk_time

        s = target_time - total_time
        if s > 0:
            # Absolute deadline so a confirmed stream of length-growth signals can't hold
            # the reply forever — see the cap above. (Tentative onset pings self-limit to
            # SPEECH_ONSET_GRACE in _more_speech_wait, well inside this cap.)
            wait_deadline = time.time() + VoiceTurnGate.MAX_TOTAL_WAIT_FOR_MORE_SPEECH
            note(f"Blocking request - will wait for {s:.2f}s (target {target_time:.2f}s) so we do not interrupt the user.")
            observers().on_waiting_for_user(True)
            time.sleep(s)
            if self.is_killed:
                observers().on_waiting_for_user(False)
                return False

            more_speech, total_time, more_target = self._more_speech_wait()
            s = more_target - total_time
            while s > 0 and more_speech:
                if time.time() >= wait_deadline:
                    note(f"Reached the {VoiceTurnGate.MAX_TOTAL_WAIT_FOR_MORE_SPEECH:.0f}s cap "
                         f"on waiting for more speech — speaking now even though the user may not be done.")
                    break
                info("We need to wait longer before continuing; the user is still speaking!")
                observers().on_waiting_for_user(True)
                time.sleep(min(s, wait_deadline - time.time()))  # never sleep past the cap
                if self.is_killed:
                    observers().on_waiting_for_user(False)
                    return False

                more_speech, total_time, more_target = self._more_speech_wait()
                s = more_target - total_time

            observers().on_waiting_for_user(False)

            trace("Done sleeping and we're not dead, lets go.")
        return self._wait_for_scribe_verdict(ctx)

    def _wait_for_scribe_verdict(self, ctx: "RequestContext | None") -> bool:
        """Hold a Whisper wake-word reply until Scribe has verified it (or a cap elapses).

        Scribe is the more accurate engine and the ONLY thing that can veto a Whisper wake-word
        false positive (its /false_positive call kills the in-flight request). But it runs a beat
        behind Whisper, so without this gate its verdict routinely lands AFTER Floxy has already
        started speaking the hallucinated reply (see LocalOutpost._on_scribe_transcript). Blocking
        here — after the natural-timing wait — lets a veto abort playback in time. Scribe only
        gets the audio once Whisper accepts, so its verdict lands ~0.8-1.5s after the accept;
        with the natural-timing wait anchored to speech end (usually zero sleep for outposts),
        this gate is typically the reply's only real hold on a wake-word fire.

        Only wake-word Whisper sends wait: a veto exists solely for those, and by this point the
        reply is already generated from Whisper's text, so a late Scribe vote can only kill a
        false positive, never improve the wording. Follow-ups, common queries, non-outpost sources
        and the old always-vote Scribe mode skip the gate — as does a Whisper-only fallback
        (ScribeWorker failed to come up: no verdict can ever arrive, so holding would just tax
        every wake-word reply with the full cap; see scribe_status) and a send flagged
        scribe_skipped (the outpost skipped Scribe on high Whisper confidence, see
        SCRIBE_SKIP_CONFIDENCE_PCT — same no-verdict-coming logic). Returns False if the request
        was killed while waiting — a Scribe veto, but just as often Scribe CONFIRMING with a
        phrase that then won the SpeechAnalyzer vote, superseding this Whisper request with a
        new one — True otherwise (Scribe confirmed, the cap elapsed, or the gate did not apply)."""
        engine = ctx.engine if ctx else None
        gate_applies = (engine == ENGINE_WHISPER and self._is_wake_word_send
                        and not self._scribe_skipped
                        and SCRIBE_VERIFY_WAKE_WORD and scribe_is_running())
        if not gate_applies:
            if self._scribe_skipped and engine == ENGINE_WHISPER and self._is_wake_word_send:
                trace("Outpost skipped Scribe on high Whisper confidence — speaking without "
                      "holding for a verdict.")
            return not self.is_killed

        gen = self._utterance_generation
        if self.speech_analyzer.scribe_has_settled(gen):
            trace(f"Scribe already weighed in on generation {gen} — no need to hold.")
            return not self.is_killed

        info(f"Holding reply for Scribe's verdict on wake-word fire (generation {gen}) "
             f"before speaking.")
        deadline = time.time() + VoiceTurnGate.MAX_WAIT_FOR_SCRIBE_VERDICT
        while time.time() < deadline:
            if self.is_killed:
                reason = self._kill_reason() or "no reason recorded"
                info(f"Request killed while holding for Scribe's verdict ({reason}) — "
                     f"aborting reply before it spoke.")
                return False
            if self.speech_analyzer.scribe_has_settled(gen):
                fine("Scribe confirmed the wake-word fire — speaking now.")
                return True
            time.sleep(0.02)
        note(f"Scribe verdict didn't arrive within {VoiceTurnGate.MAX_WAIT_FOR_SCRIBE_VERDICT:.1f}s "
             f"(network hiccup? no-vote exits settle via /scribe_settled) — speaking on Whisper alone.")
        return not self.is_killed
