import base64
import json
from unittest.mock import Mock, patch


class TestGrokVoiceSynthesizer:
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
