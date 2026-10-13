"""Microphone routes: the real blueprint on a bare Quart app with fake deps.

Replaces the route mirrors that lived in tests/unit/test_floxy_ai_server.py
(TestRouteHandlers, TestFalsePositiveKill, TestSpeechActiveOnset,
TestLengthGrowthConfirmsMoreSpeech, TestStaleScribeVoteDrop) — those re-implemented the
route bodies because the launcher module could not be imported; now the code under test is
the shipped one."""
import time
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quart import Quart

from assistant.AssistantRequest import RequestContext
from assistant.RequestProcessor import RequestData
from interfaces.voice.microphones.routes import MicrophoneDeps, MicrophoneRoutes, register
from interfaces.voice.microphones.speech_analysis.SpeechAnalyzer import SpeechAnalyzer
from interfaces.voice.VoiceTurnGate import VoiceTurnGate


def _handler(user_input, performing=True, with_gate=True):
    h = Mock()
    h.request_context = RequestContext(user_input=user_input) if user_input is not None else None
    h.is_performing_request = performing
    h.turn_gate = VoiceTurnGate(Mock(), lambda: False) if with_gate else None
    return h


@pytest.fixture
def deps():
    running = Queue(maxsize=10)
    speaker = Mock()
    speaker.get_last_identified_user.return_value = None
    monitor = Mock()
    deps = MicrophoneDeps(
        running_handlers=lambda: list(running.queue),
        speech_analyzer=SpeechAnalyzer(running_as_testsuite=False),
        play_sound=Mock(),
        wake_word="Michelle",
        configured_languages=lambda: ["en-US", "sv-SE"],
        speaker_identifier=lambda: speaker,
        stt_confidence_monitor=lambda: monitor,
        volume_controller=SimpleNamespace(current_noise_db=40.2, current_volume=55.0),
    )
    deps.running = running  # test-side handle: what running_handlers() snapshots
    return deps


@pytest.fixture
def routes(deps):
    return MicrophoneRoutes(deps)


@pytest.fixture
def app(deps):
    test_app = Quart(__name__)
    test_app.mic_routes = register(test_app, deps)
    return test_app


@pytest.fixture
def observer():
    from assistant.AssistantEvents import observers
    obs = Mock()
    observers().subscribe(obs)
    return obs


def _req(args, headers=None):
    return RequestData("GET", "/stream", dict(args), headers or {"User-Agent": "okhttp/4"})


# ---------------------------------------------------------------------------
# Routes over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRoutes:
    @pytest.mark.asyncio
    async def test_outpost_settings_returns_expected_fields(self, app):
        response = await app.test_client().get('/outpost_settings?client=kitchen')
        assert response.status_code == 200
        data = await response.get_json()
        assert data["wake_words"] == ["michelle"]
        assert data["language"] == "en-US"
        assert data["languages"] == ["en-US", "sv-SE"]
        assert data["false_positive_timeout_ms"] == 3500
        assert data["audio_processing"] == "VOICE_RECOGNITION"

    @pytest.mark.asyncio
    async def test_end_calls_playsound_end(self, app, deps):
        response = await app.test_client().get('/end')
        assert response.status_code == 204
        deps.play_sound.playsound_end.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_forwards_levels(self, app, monkeypatch):
        calls = []
        import interfaces.voice.microphones.routes as routes_mod
        monkeypatch.setattr(routes_mod, "external_info", lambda n, m: calls.append(("info", n, m)))
        monkeypatch.setattr(routes_mod, "external_fine", lambda n, m: calls.append(("fine", n, m)))
        monkeypatch.setattr(routes_mod, "external_trace", lambda n, m: calls.append(("trace", n, m)))
        client = app.test_client()
        assert (await client.get('/log?level=info&name=TestClient&message=hello')).status_code == 204
        assert (await client.get('/log?level=trace&name=TestClient&message=debug')).status_code == 204
        assert (await client.get('/log?fine=yes&name=TestClient&message=f')).status_code == 204
        assert calls == [("info", "TestClient", "hello"), ("trace", "TestClient", "debug"),
                         ("fine", "TestClient", "f")]

    @pytest.mark.asyncio
    async def test_wake_word_throttle_second_call_within_timeout(self, app, deps, observer):
        client = app.test_client()
        assert (await client.get('/wake_word_detected')).status_code == 204
        assert (await client.get('/wake_word_detected')).status_code == 204
        observer.on_wake_word_detected.assert_called_once()

    @pytest.mark.asyncio
    async def test_speech_active_arms_in_flight_gates(self, app, deps):
        running, idle = _handler("turn on"), _handler("x", performing=False)
        deps.running.put(running)
        deps.running.put(idle)
        assert (await app.test_client().get('/speech_active')).status_code == 204
        assert running.turn_gate.speech_onset_at is not None
        assert running.turn_gate.more_speech_is_incoming is None  # confirmed signal untouched
        assert idle.turn_gate.speech_onset_at is None

    @pytest.mark.asyncio
    async def test_false_positive_kills_matching_and_settles(self, app, deps):
        deps.speech_analyzer.is_best_transcription("turn on the lights", False, 0.9, "kitchen", "whisper")
        h = _handler("turn on the lights")
        deps.running.put(h)
        response = await app.test_client().get('/false_positive?query=turn%20on%20the%20lights&mic=kitchen')
        assert response.status_code == 204
        h.kill.assert_called_once()
        assert deps.speech_analyzer.scribe_has_settled(deps.speech_analyzer.utterance_generation)

    @pytest.mark.asyncio
    async def test_false_positive_without_query_is_noop(self, app, deps):
        h = _handler("turn on the lights")
        deps.running.put(h)
        assert (await app.test_client().get('/false_positive')).status_code == 204
        h.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_identify_voice_503_without_identifier(self, app, deps):
        deps.speaker_identifier = lambda: None
        response = await app.test_client().post('/identify_voice', data=b"\x00\x01")
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_identify_voice_400_without_audio(self, app):
        response = await app.test_client().post('/identify_voice', data=b"")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_identify_voice_returns_speaker(self, app, deps, monkeypatch):
        import interfaces.voice.microphones.routes as routes_mod
        monkeypatch.setattr(routes_mod, "save_pcm_wav", lambda *a, **k: "log/audio/identification/x.wav")
        deps.speaker_identifier().identify.return_value = ("Marcus", 0.93)
        response = await app.test_client().post('/identify_voice', data=b"\x00\x01" * 100,
                                                headers={"X-Sample-Rate": "16000"})
        assert response.status_code == 200
        assert await response.get_json() == {"speaker": "Marcus", "confidence": 0.93}

    @pytest.mark.asyncio
    async def test_upload_audio_session_saves_wav(self, app, monkeypatch):
        import interfaces.voice.microphones.routes as routes_mod
        saved = {}

        def fake_save(folder, query, data, sample_rate, channels, bit_depth):
            saved.update(folder=folder, query=query, size=len(data), sample_rate=sample_rate)
            return f"{folder}/{query}.wav"

        monkeypatch.setattr(routes_mod, "save_pcm_wav", fake_save)
        response = await app.test_client().post(
            '/upload_audio_session', data=b"\x00" * 64,
            headers={"X-Query": "hello", "X-Sample-Rate": "22050"})
        assert response.status_code == 200
        assert (await response.get_json())["filename"] == "hello.wav"
        assert saved == {"folder": "log/audio", "query": "hello", "size": 64, "sample_rate": 22050}


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWakeWord:
    def test_first_signal_runs_side_effects(self, routes, deps, observer):
        deps.speech_analyzer.is_best_transcription("hi", False, 0.9, "kitchen", "whisper")
        gen = deps.speech_analyzer.utterance_generation
        assert routes.handle_wake_word_detected() is True
        observer.on_wake_word_detected.assert_called_once()
        assert deps.speech_analyzer.utterance_generation == gen + 1  # clean() opened a new window

    def test_debounce_is_a_sliding_window(self, routes, observer, monkeypatch):
        import interfaces.voice.microphones.routes as routes_mod
        clock = [1000.0]
        monkeypatch.setattr(routes_mod.time, "time", lambda: clock[0])
        assert routes.handle_wake_word_detected() is True
        clock[0] += 1.0
        assert routes.handle_wake_word_detected() is False   # debounced, but refreshes the stamp
        clock[0] += 1.0                                       # 2.0s after the first, 1.0s after the refresh
        assert routes.handle_wake_word_detected() is False
        clock[0] += 1.6
        assert routes.handle_wake_word_detected() is True
        assert observer.on_wake_word_detected.call_count == 2

# ---------------------------------------------------------------------------
# kill_running_request_matching (the Scribe veto)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFalsePositiveKill:
    """The Scribe veto must kill an in-flight request ONLY when its query matches the
    vetoed phrase, so a request the server has already moved on to is never killed."""

    def test_matching_phrase_is_killed(self, routes, deps):
        h = _handler("turn on the lights")
        deps.running.put(h)
        assert routes.kill_running_request_matching("Turn on the lights.") == 1
        h.kill.assert_called_once()
        h.dialog_manager.command_processor.commands.cmd_false_positive.assert_called_once()

    def test_different_phrase_is_not_killed(self, routes, deps):
        h = _handler("play music")
        deps.running.put(h)
        assert routes.kill_running_request_matching("turn on the lights") == 0
        h.kill.assert_not_called()

    def test_non_performing_request_not_killed(self, routes, deps):
        h = _handler("turn on the lights", performing=False)
        deps.running.put(h)
        assert routes.kill_running_request_matching("turn on the lights") == 0
        h.kill.assert_not_called()

    def test_grown_phrase_is_killed_only_when_asked(self, routes, deps):
        """The outpost's accumulator appends the next fragment to the vetoed phrase, so the
        Scribe veto (include_grown) must reach the grown request; the low-confidence guard
        keeps the exact match."""
        h = _handler("Turn on the lights. Make an orange water.")
        deps.running.put(h)
        assert routes.kill_running_request_matching("Turn on the lights") == 0
        h.kill.assert_not_called()
        assert routes.kill_running_request_matching("Turn on the lights", include_grown=True) == 1
        h.kill.assert_called_once()

    def test_grown_match_needs_a_word_boundary(self, routes, deps):
        h = _handler("turn on the lightsaber")
        deps.running.put(h)
        assert routes.kill_running_request_matching("turn on the lights", include_grown=True) == 0
        h.kill.assert_not_called()

    def test_empty_phrase_kills_nothing(self, routes, deps):
        h = _handler("turn on the lights")
        deps.running.put(h)
        assert routes.kill_running_request_matching("   ") == 0
        h.kill.assert_not_called()


# ---------------------------------------------------------------------------
# accept_transcription (the /stream pre-dispatch hook)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAcceptTranscription:
    def test_empty_query_rejected(self, routes):
        assert routes.accept_transcription(_req({"query": ""}), RequestContext()) is not None

    def test_duplicate_transcription_rejected(self, routes):
        args = {"query": "turn on the kitchen lights", "confidence": "0.9", "mic": "kitchen", "engine": "whisper"}
        assert routes.accept_transcription(_req(args), RequestContext()) is None
        assert routes.accept_transcription(_req(dict(args, mic="hall")), RequestContext()) == \
            "empty/duplicate/non-best transcription"

    def test_text_source_skips_the_vote_and_clears_speaker(self, routes, deps):
        args = {"query": "hello", "text": "1"}
        assert routes.accept_transcription(_req(args), RequestContext()) is None
        assert routes.accept_transcription(_req(args), RequestContext()) is None  # no dedup for text clients
        deps.speaker_identifier().clear_last_identified_user.assert_called()

    def test_low_confidence_dropped_and_matching_request_killed(self, routes, deps):
        h = _handler("turn on the lights")
        deps.running.put(h)
        reason = routes.accept_transcription(
            _req({"query": "turn on the lights", "confidence": "0.1", "mic": "kitchen"}), RequestContext())
        assert reason == "dropped low-confidence query"
        h.kill.assert_called_once()

    def test_longer_unique_phrase_confirms_more_speech(self, routes, deps):
        """The other half of the two-signal contract from c73fe440: a genuinely longer,
        unique transcript for an in-flight request sets the CONFIRMED signal only."""
        h = _handler("turn off the")
        deps.running.put(h)
        routes.accept_transcription(
            _req({"query": "turn off the kitchen lights", "confidence": "0.9", "mic": "kitchen"}), RequestContext())
        assert h.turn_gate.more_speech_is_incoming is not None
        assert h.turn_gate.speech_onset_at is None

    def test_non_growing_phrase_does_not_confirm(self, routes, deps):
        h = _handler("turn off the lights")
        deps.running.put(h)
        routes.accept_transcription(
            _req({"query": "turn off the light", "confidence": "0.9", "mic": "kitchen"}), RequestContext())
        assert h.turn_gate.more_speech_is_incoming is None

    def test_non_unique_phrase_does_not_confirm(self, routes, deps):
        # The same command re-heard by another mic isn't a continuation, even if longer.
        routes.accept_transcription(
            _req({"query": "turn off the lights", "confidence": "0.9", "mic": "kitchen"}), RequestContext())
        h = _handler("turn off")
        deps.running.put(h)
        routes.accept_transcription(
            _req({"query": "turn off the lights", "confidence": "0.9", "mic": "hall"}), RequestContext())
        assert h.turn_gate.more_speech_is_incoming is None

    def test_requests_without_context_input_or_gate_are_skipped(self, routes, deps):
        for h in (_handler(None), _handler("turn off the", performing=False),
                  _handler("turn off the", with_gate=False)):
            deps.running.put(h)
        routes.accept_transcription(
            _req({"query": "turn off the kitchen lights", "confidence": "0.9", "mic": "kitchen"}), RequestContext())
        for h in deps.running_handlers():
            assert h.turn_gate is None or h.turn_gate.more_speech_is_incoming is None

    def test_stt_confidence_recorded_before_the_vote(self, routes, deps):
        args = {"query": "turn on the kitchen lights", "confidence": "0.9", "mic": "kitchen", "engine": "whisper"}
        routes.accept_transcription(_req(args), RequestContext())
        routes.accept_transcription(_req(dict(args, mic="hall", confidence="0.7")), RequestContext())
        mics = [c.args[0] for c in deps.stt_confidence_monitor().record.call_args_list]
        assert mics == ["kitchen", "hall"]  # the losing mic is recorded too

    def test_wake_word_folded_into_the_request(self, routes, deps, observer):
        routes.accept_transcription(
            _req({"query": "turn on the lights", "confidence": "0.9", "mic": "kitchen", "wake_word_detected": "1"}),
            RequestContext())
        observer.on_wake_word_detected.assert_called_once()

    def test_speaker_identified_by_voice_fills_context_user(self, routes, deps):
        deps.speaker_identifier().get_last_identified_user.return_value = "Marcus"
        ctx = RequestContext()
        routes.accept_transcription(_req({"query": "hello there", "confidence": "0.9", "mic": "kitchen"}), ctx)
        assert ctx.user == "Marcus"

    def test_an_unnamed_voice_speaking_is_said_so_in_the_turn_note(self, routes, deps):
        deps.speaker_identifier().get_last_identified_user.return_value = "voice-7f3a"
        deps.speaker_identifier().is_candidate.return_value = True
        ctx = RequestContext(note="in a group")
        routes.accept_transcription(_req({"query": "hello there", "confidence": "0.9", "mic": "kitchen"}), ctx)
        assert ctx.user == "voice-7f3a"
        assert ctx.note.startswith("in a group\n") and "voice-7f3a" in ctx.note and "name" in ctx.note

    def test_a_known_person_speaking_adds_no_note(self, routes, deps):
        deps.speaker_identifier().get_last_identified_user.return_value = "Marcus"
        deps.speaker_identifier().is_candidate.return_value = False
        ctx = RequestContext()
        routes.accept_transcription(_req({"query": "hello there", "confidence": "0.9", "mic": "kitchen"}), ctx)
        assert ctx.note is None

    def test_explicit_user_is_kept(self, routes, deps):
        deps.speaker_identifier().get_last_identified_user.return_value = "Marcus"
        ctx = RequestContext(user="Anna")
        routes.accept_transcription(_req({"query": "hello there", "confidence": "0.9", "mic": "kitchen"}), ctx)
        assert ctx.user == "Anna"


@pytest.mark.unit
class TestSttTiming:
    def _winning(self, routes, extra):
        args = {"query": "turn on the kitchen lights", "confidence": "0.9", "mic": "kitchen", "engine": "whisper"}
        args.update(extra)
        return routes.accept_transcription(_req(args), RequestContext())

    def test_split_timing_reported(self, routes, observer):
        assert self._winning(routes, {"stt_ms": "900", "stt_wait_ms": "400", "stt_trigger": "rms"}) is None
        observer.on_stt_timing.assert_called_once_with(0.4, 0.5, "rms")

    def test_phone_without_wait_share(self, routes, observer):
        self._winning(routes, {"stt_ms": "900"})
        observer.on_stt_timing.assert_called_once_with(-1, 0.9, "")

    def test_only_first_winner_per_utterance_counts(self, routes, deps, observer):
        self._winning(routes, {"stt_ms": "900"})
        # A slower mic winning the vote later must not overwrite the first mic's time.
        routes.accept_transcription(
            _req({"query": "turn on the kitchen lights please", "confidence": "0.95", "mic": "hall",
                  "engine": "whisper", "stt_ms": "1500"}), RequestContext())
        observer.on_stt_timing.assert_called_once()
        deps.speech_analyzer.clean()
        self._winning(routes, {"stt_ms": "700"})
        assert observer.on_stt_timing.call_count == 2

    def test_malformed_value_ignored(self, routes, observer):
        self._winning(routes, {"stt_ms": "abc"})
        observer.on_stt_timing.assert_not_called()

    def test_no_stt_ms_no_event(self, routes, observer):
        self._winning(routes, {})
        observer.on_stt_timing.assert_not_called()


@pytest.mark.unit
class TestStaleScribeVoteDrop:
    """A late Scribe vote whose verified_phrase correlator misses the current utterance window
    is provably stale — back-to-back wake words inside Scribe's ~0.76s cloud round-trip mean
    clean() has already wiped the window — and must be dropped BEFORE the transcription vote,
    where the old phrase would look fresh and could re-fire the previous command."""

    def _scribe_vote(self, routes, query, verified_phrase=""):
        args = {"query": query, "confidence": "0.97", "mic": "kitchen", "engine": "scribe"}
        if verified_phrase:
            args["verified_phrase"] = verified_phrase
        return routes.accept_transcription(_req(args), RequestContext())

    def test_stale_correlated_vote_dropped_before_voting(self, routes, deps):
        analyzer = deps.speech_analyzer
        # Utterance N wins the vote and its Scribe job launches; a new wake word
        # (clean()) opens utterance N+1 before the Scribe result returns.
        assert analyzer.is_best_transcription("turn on the kitchen lights", False, 0.9, "kitchen", "whisper")
        analyzer.clean()
        assert analyzer.is_best_transcription("hi", False, 0.5, "kitchen", "whisper")

        reason = self._scribe_vote(routes, "turn on the kitchen lights.", verified_phrase="turn on the kitchen lights")

        assert reason == "stale Scribe vote for a previous utterance"
        # The stale vote must not have released utterance N+1's verdict gate either.
        assert not analyzer.scribe_has_settled(analyzer.utterance_generation)

    def test_stale_vote_would_win_the_new_utterances_vote(self, routes, deps):
        """Documents why the drop is load-bearing: had the stale vote fallen through to
        is_best_transcription, the long high-confidence old phrase beats the new
        utterance's short one and the previous command fires twice."""
        analyzer = deps.speech_analyzer
        assert analyzer.is_best_transcription("turn on the kitchen lights", False, 0.9, "kitchen", "whisper")
        analyzer.clean()
        assert analyzer.is_best_transcription("hi", False, 0.5, "kitchen", "whisper")

        assert self._scribe_vote(routes, "turn on the kitchen lights") is None  # no correlator → falls through and wins

    def test_matching_correlator_settles_gate_and_votes(self, routes, deps):
        analyzer = deps.speech_analyzer
        assert analyzer.is_best_transcription("turn on the kitchen lights", False, 0.9, "kitchen", "whisper")
        gen = analyzer.utterance_generation

        reason = self._scribe_vote(routes, "turn on the kitchen light", verified_phrase="turn on the kitchen lights")

        assert reason is None
        assert analyzer.scribe_has_settled(gen)

    def test_correlator_less_miss_falls_through_to_vote(self, routes, deps):
        analyzer = deps.speech_analyzer
        assert analyzer.is_best_transcription("turn on the kitchen lights", False, 0.9, "kitchen", "whisper")

        reason = self._scribe_vote(routes, "turning on the kitchen lights")

        assert reason is None
        assert not analyzer.scribe_has_settled(analyzer.utterance_generation)


@pytest.mark.unit
class TestScribeConfirmationOutweighsVeto:
    """Several mics hear one utterance and each runs its own Scribe verify. Scribe can garble
    the wake word on one mic while hearing it on another, so a veto is ignored once another
    mic's Scribe has confirmed the wake word for the same Whisper phrase."""

    WHISPER = "defrost the car"

    def _fire(self, deps):
        assert deps.speech_analyzer.is_best_transcription(self.WHISPER, False, 0.5, "hall", "whisper")
        h = _handler(self.WHISPER)
        deps.running.put(h)
        return h

    def _scribe_vote(self, routes, confirmed=True, verified_phrase=WHISPER):
        args = {"query": "would approach the car", "confidence": "0.48", "mic": "hall",
                "engine": "scribe", "verified_phrase": verified_phrase}
        if confirmed:
            args["wake_word_confirmed"] = "1"
        return routes.accept_transcription(_req(args), RequestContext())

    async def _veto(self, app):
        return await app.test_client().get('/false_positive?query=defrost%20the%20car&mic=kitchen')

    @pytest.mark.asyncio
    async def test_veto_after_another_mics_confirmation_does_not_kill(self, app, deps):
        h = self._fire(deps)
        self._scribe_vote(app.mic_routes)
        assert (await self._veto(app)).status_code == 204
        h.kill.assert_not_called()
        h.dialog_manager.command_processor.commands.cmd_false_positive.assert_not_called()
        assert deps.speech_analyzer.scribe_has_settled(deps.speech_analyzer.utterance_generation)

    @pytest.mark.asyncio
    async def test_follow_up_scribe_vote_is_not_a_confirmation(self, app, deps):
        h = self._fire(deps)
        self._scribe_vote(app.mic_routes, confirmed=False)
        await self._veto(app)
        h.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirmation_does_not_carry_into_the_next_utterance(self, app, deps):
        self._fire(deps)
        self._scribe_vote(app.mic_routes)
        deps.speech_analyzer.clean()
        h = self._fire(deps)
        await self._veto(app)
        h.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_confirmation_for_a_previous_utterance_is_not_recorded(self, app, deps):
        assert deps.speech_analyzer.is_best_transcription("open the garage", False, 0.5, "hall", "whisper")
        deps.speech_analyzer.clean()
        h = self._fire(deps)
        self._scribe_vote(app.mic_routes, verified_phrase="open the garage")
        await self._veto(app)
        h.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirmation_of_a_different_whisper_phrase_does_not_protect(self, app, deps):
        """Mics whose Whisper transcripts disagree are each hallucinating over background
        speech; one Scribe hearing the wake word on its own phrase says nothing about the
        phrase another mic fired on."""
        analyzer = deps.speech_analyzer
        assert analyzer.is_best_transcription("read a bedtime story", False, 0.44, "hall", "whisper")
        self._scribe_vote(app.mic_routes, verified_phrase="read a bedtime story")
        analyzer.is_best_transcription(self.WHISPER, False, 0.45, "kitchen", "whisper")
        h = _handler(self.WHISPER)
        deps.running.put(h)
        await self._veto(app)
        h.kill.assert_called_once()
