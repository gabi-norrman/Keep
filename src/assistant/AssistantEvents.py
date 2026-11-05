"""Events between the assistant core and its interfaces.

Two channels:

* **Per-request stream events** (`AssistantEvent` subclasses below) — what
  `Assistant.stream()` yields to the interface that made the request. Interfaces render
  them (chat interfaces: spinner + one bubble per RoundEnd; HTTP: the legacy
  wire bytes; voice: audio chunks to the speaker).
* **Observer hub** (`observers()`) — process-wide fan-out of metrics/dialog events for
  dashboards (the TUI). The core never imports an interface; with no subscriber an event
  is simply dropped.
"""
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from common.logging import error, trace

if TYPE_CHECKING:
    from assistant.AssistantRequest import RequestContext


# --- per-request stream events -----------------------------------------------------------

@dataclass(frozen=True)
class AssistantEvent:
    """Base of everything `Assistant.stream()` yields."""


@dataclass(frozen=True)
class UserIdentified(AssistantEvent):
    """Who is speaking, as soon as the assistant knows (first event when known)."""
    user: str


@dataclass(frozen=True)
class Token(AssistantEvent):
    """Filtered reply text as it streams (commands and <thinking> stripped)."""
    text: str


@dataclass(frozen=True)
class Thinking(AssistantEvent):
    """A chunk of the model's <thinking> block, live. `output_tokens` is the running
    output-token estimate for this request (drives the Telegram "Thinking (N tokens)" spinner)."""
    text: str
    output_tokens: int = 0


@dataclass(frozen=True)
class ThinkingDone(AssistantEvent):
    duration: float


@dataclass(frozen=True)
class CommandRan(AssistantEvent):
    name: str
    params_display: str


@dataclass(frozen=True)
class AudioChunk(AssistantEvent):
    """TTS audio for the client to play (only when the client, not the server, plays)."""
    data: bytes


@dataclass(frozen=True)
class Notice(AssistantEvent):
    """Out-of-band text for the user: "Request denied.", transport-failure apologies."""
    text: str


@dataclass(frozen=True)
class RoundEnd(AssistantEvent):
    """One LLM round finished (the user's turn, then one per command follow-up round).
    `raw_text` is the round's unfiltered LLM output ("" when the round failed) for consumers
    that do not stream tokens, e.g. the prompt test suite. Today's `\x1e` on the wire."""
    raw_text: str = ""


@dataclass(frozen=True)
class Done(AssistantEvent):
    """The request completed (history updated, DB row written). Not emitted when the
    request was killed or failed. `full_text` is the final round's raw LLM output."""
    full_text: str


# --- observer hub -------------------------------------------------------------------------


class AssistantObserver:
    """Every callback is a no-op; implementers override only what they need."""

    # --- request lifecycle ---------------------------------------------------------
    def on_request_received(self, context: "RequestContext", mic: str = "",
                            confidence: Optional[float] = None, rejected: Optional[str] = None):
        """A transcription arrived over /stream and went through the cross-mic vote: who
        asked what, from which mic and how sure its STT was; `rejected` names why it will
        not be dispatched (a duplicate from another mic, low confidence), None when it goes
        on to the assistant. Every text the server hears, so the TUI can show it at once —
        the user row lands only when the exchange is stored, and a killed request never
        gets one."""

    def on_first_token(self, seconds: float, from_cache: bool):
        """First LLM token arrived `seconds` after the request was sent."""

    def on_reply_started(self, seconds_after_first_token: float):
        """First user-facing reply token (after any <thinking> block)."""

    def on_first_sound_chunk(self, seconds: float, from_cache: bool):
        """First TTS audio chunk, measured from the start of synthesis."""

    def on_speaker_output_started(self, seconds: float):
        """First chunk was written to the server speaker."""

    def on_waiting_for_user(self, waiting: bool):
        """The reply is being held back because the user may still be talking."""

    # --- stream ----------------------------------------------------------------------
    def on_tokens(self, text: str):
        """Filtered reply text as it streams (commands and thinking stripped)."""

    def on_thinking_chunk(self, text: str):
        pass

    def on_thinking_done(self, duration: float):
        pass

    # --- commands --------------------------------------------------------------------
    def on_command_ran(self, name: str, params_display: str):
        pass

    def on_command_notification(self, text: str):
        """Human-readable description of a command that just ran."""

    # --- prompt cache ----------------------------------------------------------------
    def on_cache_break(self, report):
        """An LLM request read back much less of its prompt from the server's cache than the
        request before it sent: a `CacheBreak` (assistant.llm.CacheBreakTracker) saying which
        line first differs, or why else — the Home tab's debug row."""

    # --- outposts / server audio -----------------------------------------------------
    def on_outpost_latency(self, addr, rtt_ms: float):
        pass

    def on_server_volume(self, noise_db: float, target_volume: float):
        pass

    # --- microphones -----------------------------------------------------------------
    def on_wake_word_detected(self):
        """A (debounced) wake word opened a fresh conversation."""

    def on_stt_timing(self, time_to_silence_detect: float, time_to_first_word: float, stt_trigger: str):
        """STT latency of the utterance's winning transcription: endpointing share (-1 when
        the client does not report it), transcription share, and which gate started the
        decode ("rms"/"vad"/"")."""


class ObserverHub(AssistantObserver):
    """Fans every event out to the subscribed observers, in subscription order.

    An observer must never break a request: exceptions are logged and swallowed.
    """

    def __init__(self):
        self._observers: List[AssistantObserver] = []
        self._lock = threading.Lock()
        self._reported_unobserved = set()

    def subscribe(self, observer: AssistantObserver):
        with self._lock:
            if observer in self._observers:
                trace(f"Observer {type(observer).__name__} already subscribed, nothing to do.")
                return
            self._observers.append(observer)
        trace(f"Observer {type(observer).__name__} subscribed to assistant events.")

    def unsubscribe(self, observer: AssistantObserver):
        with self._lock:
            if observer not in self._observers:
                trace(f"Observer {type(observer).__name__} was not subscribed, nothing to do.")
                return
            self._observers.remove(observer)

    def clear(self):
        with self._lock:
            self._observers.clear()
            self._reported_unobserved.clear()

    @property
    def observers(self) -> List[AssistantObserver]:
        with self._lock:
            return list(self._observers)

    def _emit(self, event: str, *args):
        targets = self.observers
        if not targets:
            if event not in self._reported_unobserved:
                self._reported_unobserved.add(event)
                trace(f"No observer subscribed for assistant event '{event}' (reported once).")
            return
        for observer in targets:
            try:
                getattr(observer, event)(*args)
            except Exception as e:
                error(f"Observer {type(observer).__name__}.{event} failed: {e}")

    def on_request_received(self, context: "RequestContext", mic: str = "",
                            confidence: Optional[float] = None, rejected: Optional[str] = None):
        self._emit("on_request_received", context, mic, confidence, rejected)

    def on_first_token(self, seconds: float, from_cache: bool):
        self._emit("on_first_token", seconds, from_cache)

    def on_reply_started(self, seconds_after_first_token: float):
        self._emit("on_reply_started", seconds_after_first_token)

    def on_first_sound_chunk(self, seconds: float, from_cache: bool):
        self._emit("on_first_sound_chunk", seconds, from_cache)

    def on_speaker_output_started(self, seconds: float):
        self._emit("on_speaker_output_started", seconds)

    def on_waiting_for_user(self, waiting: bool):
        self._emit("on_waiting_for_user", waiting)

    def on_tokens(self, text: str):
        self._emit("on_tokens", text)

    def on_thinking_chunk(self, text: str):
        self._emit("on_thinking_chunk", text)

    def on_thinking_done(self, duration: float):
        self._emit("on_thinking_done", duration)

    def on_command_ran(self, name: str, params_display: str):
        self._emit("on_command_ran", name, params_display)

    def on_command_notification(self, text: str):
        self._emit("on_command_notification", text)

    def on_cache_break(self, report):
        self._emit("on_cache_break", report)

    def on_outpost_latency(self, addr, rtt_ms: float):
        self._emit("on_outpost_latency", addr, rtt_ms)

    def on_server_volume(self, noise_db: float, target_volume: float):
        self._emit("on_server_volume", noise_db, target_volume)

    def on_wake_word_detected(self):
        self._emit("on_wake_word_detected")

    def on_stt_timing(self, time_to_silence_detect: float, time_to_first_word: float, stt_trigger: str):
        self._emit("on_stt_timing", time_to_silence_detect, time_to_first_word, stt_trigger)


_hub = ObserverHub()


def observers() -> ObserverHub:
    """The process-wide hub. Phase 4 hangs this off `Assistant`."""
    return _hub
