import ormsgpack
from unittest.mock import Mock, patch

from websocket import ABNF


def _configure(mock_config, tts_fish=None, api_key="test-key"):
    values = {"tts.fish": tts_fish or {}, "api_keys.fish_audio": api_key}
    mock_config.get.side_effect = lambda key, default=None: values.get(key, default)


class TestFishAudioVoiceSynthesizer:
    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_voice_id_reads_reference_id_from_config(self, mock_config):
        _configure(mock_config, {"reference_id": "abc123"})
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        assert FishAudioVoiceSynthesizer.voice_id() == "abc123"

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_build_start_event_format(self, mock_config):
        _configure(mock_config, {"reference_id": "abc123"})
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        event = vs._build_start_event()

        assert event["event"] == "start"
        request = event["request"]
        assert request["format"] == "pcm"
        assert request["sample_rate"] == 24000
        assert request["latency"] == "balanced"
        assert request["chunk_length"] == 100
        assert request["prosody"]["speed"] == 1.0
        assert request["reference_id"] == "abc123"

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_build_start_event_omits_reference_id_when_unset(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        assert "reference_id" not in vs._build_start_event()["request"]

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_ws_url_and_headers(self, mock_config):
        _configure(mock_config, {"model": "s2.1-pro"})
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        assert vs._get_ws_url() == "wss://api.fish.audio/v1/tts/live"
        headers = vs._get_ws_headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["model"] == "s2.1-pro"

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_on_open_sends_msgpack_start_event(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs.ws = Mock()
        # Prevent the thread from starting to avoid side effects
        with patch('threading.Thread'):
            vs._on_open(vs.ws)

        assert vs.is_connected is True
        vs.ws.send.assert_called_once()
        args, kwargs = vs.ws.send.call_args
        assert kwargs.get("opcode") == ABNF.OPCODE_BINARY
        sent = ormsgpack.unpackb(args[0])
        assert sent["event"] == "start"
        assert sent["request"]["format"] == "pcm"

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_on_message_audio_queues_pcm_bytes(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs._on_message(None, ormsgpack.packb({"event": "audio", "audio": b"\x01\x02\x03\x04"}))
        assert vs.audio_queue.get_nowait() == b"\x01\x02\x03\x04"

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_on_message_finish_stop_sends_none(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        with patch('time.sleep') as mock_sleep:
            vs._on_message(None, ormsgpack.packb({"event": "finish", "reason": "stop"}))
            mock_sleep.assert_called_once()
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_on_message_finish_error_sends_none_without_grace(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        with patch('time.sleep') as mock_sleep:
            vs._on_message(None, ormsgpack.packb({"event": "finish", "reason": "error"}))
            mock_sleep.assert_not_called()
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_on_message_garbage_does_not_raise(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs._on_message(None, b"\xc1 not msgpack")
        vs._on_message(None, "unexpected text frame")
        assert vs.audio_queue.empty()

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_on_error_queues_none(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs._on_error(None, RuntimeError("test"))
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_feed_text_thread_sends_text_then_stop(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent = []
        vs.ws.send = lambda data, **kwargs: sent.append(ormsgpack.unpackb(data))

        vs.text_queue.put("Hello world")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        assert sent == [{"event": "text", "text": "Hello world"}, {"event": "stop"}]

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_feed_text_thread_skips_whitespace_only(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent = []
        vs.ws.send = lambda data, **kwargs: sent.append(ormsgpack.unpackb(data))

        vs.text_queue.put("   ")
        vs.text_queue.put("")
        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        text_events = [m for m in sent if m["event"] == "text"]
        assert len(text_events) == 1
        assert text_events[0]["text"] == "Hello"

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_feed_text_thread_handles_closed_connection(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs.is_connected = True
        vs.is_closed = True  # Closed before starting
        vs.ws = Mock()

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        # Should exit early without error
        vs._feed_text_thread()

        vs.ws.send.assert_not_called()

    @patch('interfaces.voice.tts.FishAudioVoiceSynthesizer.config')
    def test_feed_text_thread_send_failure_unblocks_audio_queue(self, mock_config):
        _configure(mock_config)
        from interfaces.voice.tts.FishAudioVoiceSynthesizer import FishAudioVoiceSynthesizer

        vs = FishAudioVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        vs.ws.send = Mock(side_effect=RuntimeError("socket dead"))

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        assert vs.audio_queue.get_nowait() is None
