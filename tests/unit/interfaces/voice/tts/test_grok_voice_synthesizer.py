import base64
import json
from unittest.mock import Mock, patch


class TestGrokVoiceSynthesizer:
    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_voice_id_returns_ara(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        assert GrokVoiceSynthesizer.voice_id() == "ara"

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_ws_url_contains_voice_and_sample_rate(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        url = vs._get_ws_url()
        assert "voice=ara" in url
        assert "sample_rate=24000" in url
        assert "codec=pcm" in url

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_ws_headers_contain_auth(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        headers = vs._get_ws_headers()
        assert headers["Authorization"] == "Bearer test-key"

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_on_message_audio_delta_queues_decoded_audio(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        audio_data = b"test audio bytes"
        b64_audio = base64.b64encode(audio_data).decode()
        message = json.dumps({"type": "audio.delta", "delta": b64_audio})
        vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() == audio_data

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_on_message_audio_done_sends_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        message = json.dumps({"type": "audio.done"})
        with patch('time.sleep'):
            vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_on_message_error_queues_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        message = json.dumps({"type": "error", "message": "something broke"})
        vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_on_open_sets_connected_and_starts_thread(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        assert vs.is_connected is False
        with patch('threading.Thread') as mock_thread:
            vs._on_open(Mock())
        assert vs.is_connected is True
        mock_thread.assert_called_once()

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_on_error_queues_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        vs._on_error(None, RuntimeError("test"))
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_feed_text_thread_sends_text_delta_and_done(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello world")
        vs.text_queue.put(None)
        vs._feed_text_thread()

        text_deltas = [m for m in sent_messages if m.get("type") == "text.delta"]
        done_msgs = [m for m in sent_messages if m.get("type") == "text.done"]
        assert len(text_deltas) >= 1
        assert text_deltas[0]["delta"] == "Hello world"
        assert len(done_msgs) == 1

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_feed_text_thread_skips_whitespace_only(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("   ")
        vs.text_queue.put("")
        vs.text_queue.put("Hello")
        vs.text_queue.put(None)
        vs._feed_text_thread()

        text_deltas = [m for m in sent_messages if m.get("type") == "text.delta"]
        assert len(text_deltas) == 1
        assert "Hello" in text_deltas[0]["delta"]

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_feed_text_thread_buffers_incomplete_xml_tag(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello <laugh")
        vs.text_queue.put("er>world")
        vs.text_queue.put(None)
        vs._feed_text_thread()

        text_deltas = [m for m in sent_messages if m.get("type") == "text.delta"]
        combined = ''.join(m["delta"] for m in text_deltas)
        assert "<laughter>" in combined or "laugher" in combined
        assert "Hello" in combined
        assert "world" in combined

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_feed_text_thread_handles_closed_connection(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        vs.is_connected = True
        vs.is_closed = True
        vs.ws = Mock()

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)
        vs._feed_text_thread()

        vs.ws.send.assert_not_called()

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_feed_text_thread_flushes_buffer_on_done(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        # Send incomplete tag then immediately end — should flush buffer
        vs.text_queue.put("Trailing text <tag")
        vs.text_queue.put(None)
        vs._feed_text_thread()

        text_deltas = [m for m in sent_messages if m.get("type") == "text.delta"]
        combined = ''.join(m["delta"] for m in text_deltas)
        assert "Trailing text <tag" in combined

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_integration_properties(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        assert vs.name == "Grok TTS"
        assert vs.config_keys == ["api_keys.grok"]

    @patch('interfaces.voice.tts.GrokVoiceSynthesizer.config')
    def test_check_health_is_disabled_and_always_healthy(self, mock_config):
        # The x.ai voices probe was removed because it returned spurious empty-body
        # HTTP 500s; check_health() now reports healthy without any network call.
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.GrokVoiceSynthesizer import GrokVoiceSynthesizer
        vs = GrokVoiceSynthesizer()
        assert vs.check_health() is True
