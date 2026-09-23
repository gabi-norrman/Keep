import pytest
from unittest.mock import patch

from assistant.AssistantEvents import AssistantObserver, ObserverHub


class Recorder(AssistantObserver):
    def __init__(self, name, log):
        self.name = name
        self.log = log

    def on_tokens(self, text):
        self.log.append((self.name, "tokens", text))

    def on_first_token(self, seconds, from_cache):
        self.log.append((self.name, "first_token", seconds, from_cache))


class Exploding(AssistantObserver):
    def on_tokens(self, text):
        raise RuntimeError("boom")


@pytest.mark.unit
class TestObserverHub:
    def test_fan_out_in_subscription_order(self):
        hub, log = ObserverHub(), []
        hub.subscribe(Recorder("a", log))
        hub.subscribe(Recorder("b", log))
        hub.on_tokens("hi")
        assert log == [("a", "tokens", "hi"), ("b", "tokens", "hi")]

    def test_unsubscribed_observer_stops_receiving(self):
        hub, log = ObserverHub(), []
        a = Recorder("a", log)
        hub.subscribe(a)
        hub.unsubscribe(a)
        hub.on_tokens("hi")
        assert log == []

    def test_subscribe_twice_registers_once(self):
        hub, log = ObserverHub(), []
        a = Recorder("a", log)
        hub.subscribe(a)
        hub.subscribe(a)
        hub.on_tokens("x")
        assert len(log) == 1

    def test_unsubscribe_unknown_is_noop(self):
        ObserverHub().unsubscribe(AssistantObserver())

    def test_exception_in_one_observer_does_not_stop_others(self):
        hub, log = ObserverHub(), []
        hub.subscribe(Exploding())
        hub.subscribe(Recorder("b", log))
        with patch("assistant.AssistantEvents.error") as err:
            hub.on_tokens("hi")
        assert log == [("b", "tokens", "hi")]
        assert "boom" in err.call_args[0][0]

    def test_arguments_forwarded(self):
        hub, log = ObserverHub(), []
        hub.subscribe(Recorder("a", log))
        hub.on_first_token(0.42, True)
        assert log == [("a", "first_token", 0.42, True)]

    def test_default_observer_ignores_every_event(self):
        hub = ObserverHub()
        hub.subscribe(AssistantObserver())
        hub.on_request_received(object(), "kitchen", 0.9, None)
        hub.on_first_token(0.1, False)
        hub.on_reply_started(0.2)
        hub.on_first_sound_chunk(0.3, False)
        hub.on_speaker_output_started(0.05)
        hub.on_waiting_for_user(True)
        hub.on_tokens("t")
        hub.on_thinking_chunk("th")
        hub.on_thinking_done(1.0)
        hub.on_command_ran("turn_on", "kitchen")
        hub.on_command_notification("Turning on kitchen")
        hub.on_outpost_latency(("127.0.0.1", 1), 12.0)
        hub.on_server_volume(40.0, 50)
        hub.on_wake_word_detected()
        hub.on_stt_timing(0.4, 0.3, "vad")

    def test_no_subscriber_logged_once_per_event(self):
        hub = ObserverHub()
        with patch("assistant.AssistantEvents.trace") as tr:
            hub.on_tokens("a")
            hub.on_tokens("b")
            hub.on_thinking_done(1.0)
        messages = [c[0][0] for c in tr.call_args_list if "No observer" in c[0][0]]
        assert len(messages) == 2

    def test_clear_removes_all(self):
        hub, log = ObserverHub(), []
        hub.subscribe(Recorder("a", log))
        hub.clear()
        hub.on_tokens("x")
        assert log == []
