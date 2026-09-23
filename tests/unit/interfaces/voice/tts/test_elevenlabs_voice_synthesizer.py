import base64
import json
from unittest.mock import Mock, patch


class TestElevenLabsVoiceSynthesizer:
    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_voice_id_day(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer
        from assistant.prompt.TimeOfDay import TimeOfDay

        with patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.TimeOfDay') as mock_tod:
            mock_tod.get_current.return_value = TimeOfDay.DAY
            mock_tod.DAY = TimeOfDay.DAY
            voice_id = ElevenLabsVoiceSynthesizer.voice_id()
            assert voice_id == "QtY3JBOUKEB5xzrRfOKc"  # Maisie

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_voice_id_night(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer
        from assistant.prompt.TimeOfDay import TimeOfDay

        with patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.TimeOfDay') as mock_tod:
            mock_tod.get_current.return_value = TimeOfDay.NIGHT
            mock_tod.DAY = TimeOfDay.DAY
            voice_id = ElevenLabsVoiceSynthesizer.voice_id()
            assert voice_id == "geVuXdUpU0lgUukWJCUE"  # Shannon

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_on_message_queues_decoded_audio(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        audio_data = b"test audio bytes"
        b64_audio = base64.b64encode(audio_data).decode()
        message = json.dumps({"audio": b64_audio, "isFinal": False})

        vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() == audio_data

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_on_message_final_sends_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        message = json.dumps({"audio": None, "isFinal": True})

        with patch('time.sleep'):
            vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_on_open_sends_bos_config(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        mock_ws = Mock()
        # Prevent the thread from starting to avoid side effects
        with patch('threading.Thread'):
            vs._on_open(mock_ws)

        assert vs.is_connected is True
        mock_ws.send.assert_called_once()
        sent_data = json.loads(mock_ws.send.call_args[0][0])
        assert sent_data['bos'] is True
        assert sent_data['xi_api_key'] == "test-key"

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_on_error_queues_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        vs._on_error(None, RuntimeError("test"))
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_feed_text_thread_sends_json_envelope(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello world")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Should have sent text message with proper format
        text_messages = [m for m in sent_messages if m.get('text', '').strip()]
        assert len(text_messages) >= 1
        assert 'text' in text_messages[0]
        assert text_messages[0]['text'] == "Hello world"

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_feed_text_thread_sets_try_trigger_generation(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        text_messages = [m for m in sent_messages if m.get('text', '').strip()]
        assert text_messages[0].get('try_trigger_generation') is True

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_feed_text_thread_sets_eos_on_last(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Last message should have eos=True
        eos_messages = [m for m in sent_messages if m.get('eos')]
        assert len(eos_messages) == 1
        assert eos_messages[0]['text'] == ""  # EOS message has empty text

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_feed_text_thread_skips_whitespace_only(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("   ")
        vs.text_queue.put("")
        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Should only have one text message (Hello) plus EOS
        text_messages = [m for m in sent_messages if m.get('text', '').strip()]
        assert len(text_messages) == 1
        assert text_messages[0]['text'] == "Hello"

    @patch('interfaces.voice.tts.ElevenLabsVoiceSynthesizer.config')
    def test_feed_text_thread_handles_closed_connection(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer

        vs = ElevenLabsVoiceSynthesizer()
        vs.is_connected = True
        vs.is_closed = True  # Closed before starting
        vs.ws = Mock()

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        # Should exit early without error
        vs._feed_text_thread()

        vs.ws.send.assert_not_called()
