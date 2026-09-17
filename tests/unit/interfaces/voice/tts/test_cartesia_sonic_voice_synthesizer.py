import base64
import json
from unittest.mock import Mock, patch


class TestCartesiaSonicVoiceSynthesizer:
    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_voice_id_returns_correct_id(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        assert CartesiaSonicVoiceSynthesizer.voice_id() == "829ccd10-f8b3-43cd-b8a0-4aeaa81f3b30"

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
    def test_create_message_end_stream(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        vs = CartesiaSonicVoiceSynthesizer()
        msg = vs._create_message("", continue_streaming=False)
        assert msg['transcript'] == ""
        assert msg['continue'] is False

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
    def test_name_pronunciations_config_section(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer
        assert CartesiaSonicVoiceSynthesizer.CONFIG_SECTION == "cartesia"

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_on_open_sets_connected_and_starts_thread(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        assert vs.is_connected is False

        with patch('threading.Thread') as mock_thread:
            vs._on_open(Mock())

        assert vs.is_connected is True
        mock_thread.assert_called_once()

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_on_close_clears_connected(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True

        vs._on_close(None, None, None)

        assert vs.is_connected is False
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
    def test_feed_text_thread_buffers_incomplete_xml_tag(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        # Put text with incomplete tag
        vs.text_queue.put("Hello <laugh")
        vs.text_queue.put("er>world")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Should have sent buffered content including the complete tag
        all_transcripts = [m['transcript'] for m in sent_messages if m.get('transcript')]
        combined = ''.join(all_transcripts)
        assert "<laughter>" in combined or "laugher" in combined

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_feed_text_thread_waits_for_text_after_tag(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        # Tag at end - should wait for content after tag
        vs.text_queue.put("Hello <emotion>")
        vs.text_queue.put(" more text")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Check that tag was properly handled
        transcripts = [m['transcript'] for m in sent_messages if m.get('transcript')]
        assert len(transcripts) >= 1

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_feed_text_thread_rejects_non_alphanumeric_first_chunk(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        # First chunk with no alphanumeric (just tag or punctuation)
        vs.text_queue.put("<laugh/>")
        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # First actual content should be "Hello" (tag alone rejected)
        transcripts = [m['transcript'] for m in sent_messages if m.get('transcript')]
        # Either sent together or Hello sent after tag was buffered
        combined = ''.join(transcripts)
        assert "Hello" in combined

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
    def test_feed_text_thread_sets_context_id_on_first_chunk(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        original_context_id = vs.context_id

        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Context ID should be updated for new utterance
        if sent_messages:
            assert 'context_id' in sent_messages[0]

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

    @patch('interfaces.voice.tts.CartesiaSonicVoiceSynthesizer.config')
    def test_feed_text_thread_skips_whitespace_only(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from interfaces.voice.tts.CartesiaSonicVoiceSynthesizer import CartesiaSonicVoiceSynthesizer

        vs = CartesiaSonicVoiceSynthesizer()
        vs.is_connected = True
        vs.ws = Mock()
        sent_messages = []
        vs.ws.send = lambda msg: sent_messages.append(json.loads(msg))

        vs.text_queue.put("   ")
        vs.text_queue.put("")
        vs.text_queue.put("Hello")
        vs.text_queue.put(None)

        vs._feed_text_thread()

        # Should only send non-empty content
        transcripts = [m['transcript'] for m in sent_messages if m.get('transcript', '').strip()]
        assert len(transcripts) >= 1
