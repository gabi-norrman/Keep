import base64
import json
from unittest.mock import Mock, patch


class TestCartesiaSonicVoiceSynthesizer:
    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_volume_clamped_low(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer(volume=0.1)
        assert vs.volume == 0.5

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_volume_clamped_high(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer(volume=5.0)
        assert vs.volume == 2.0

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_create_message_format(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer()
        msg = vs._create_message("Hello world", continue_streaming=True)
        assert msg['model_id'] == "sonic-3"
        assert msg['transcript'] == "Hello world"
        assert msg['voice']['mode'] == 'id'
        assert msg['voice']['id'] == vs.voice_id()
        assert msg['output_format']['container'] == 'raw'
        assert msg['output_format']['encoding'] == 'pcm_s16le'
        assert msg['output_format']['sample_rate'] == 24000
        assert msg['continue'] is True

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_on_message_chunk_queued(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer()

        audio_data = b"audio bytes"
        b64_audio = base64.b64encode(audio_data).decode()
        message = json.dumps({"type": "chunk", "data": b64_audio, "done": False})

        vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() == audio_data

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_on_message_done_sends_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer()

        message = json.dumps({"type": "done"})
        with patch('time.sleep'):
            vs._on_message(None, message)
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_on_error_queues_none(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer()
        vs._on_error(None, RuntimeError("test"))
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_on_message_chunk_with_done_flag(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        audio_data = b"final_audio"
        b64_audio = base64.b64encode(audio_data).decode()
        message = json.dumps({"type": "chunk", "data": b64_audio, "done": True})

        with patch('time.sleep'):
            vs._on_message(None, message)

        assert vs.audio_queue.get_nowait() == audio_data
        assert vs.audio_queue.get_nowait() is None
        assert vs._saw_done is True

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_feed_text_thread_sends_continue_false_on_last(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello world")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Last message should have continue=False
        assert len(sent_messages) >= 1
        last_msg = sent_messages[-1]
        assert last_msg['continue'] is False

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_feed_text_thread_ends_audio_when_no_text(self, mock_config):
        """Regression: an LLM reply that is a command call only (no spoken text)
        must still terminate the audio stream. Otherwise Cartesia never sends a
        `done`, audio_queue never gets its None sentinel, and the request thread
        deadlocks forever."""
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        # No text chunks at all - just the end-of-stream sentinel
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Nothing sent to Cartesia, but the audio stream is terminated so the
        # downstream _audio_generator exits instead of blocking.
        assert sent_messages == []
        assert vs.audio_queue.get_nowait() is None
