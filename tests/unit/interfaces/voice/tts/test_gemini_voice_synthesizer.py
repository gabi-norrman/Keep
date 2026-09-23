from unittest.mock import Mock, patch, MagicMock


class TestGeminiVoiceSynthesizer:
    @staticmethod
    def _capture_streaming_config(vs):
        from unittest.mock import Mock as _Mock
        mock_tts_module = MagicMock()
        captured = {}

        def capture_voice(**kw):
            captured.update(kw)
            return kw
        mock_tts_module.VoiceSelectionParams = _Mock(side_effect=capture_voice)
        mock_tts_module.StreamingSynthesizeConfig = _Mock(side_effect=lambda **kw: kw)
        mock_tts_module.StreamingSynthesizeRequest = _Mock(side_effect=lambda **kw: kw)
        with patch.dict('sys.modules', {'google.cloud.texttospeech': mock_tts_module}):
            vs._build_streaming_config()
        return captured

    @patch('common.Language.config')
    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_streaming_config_language_uses_request_locale(self, mock_gemini_config, mock_base_config):
        # Global config is en-US, but a Swedish request must speak sv-SE.
        mock_base_config.get.return_value = "en-US"
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.set_request_language("sv-SE")
        captured = self._capture_streaming_config(vs)
        assert captured['language_code'] == "sv-SE"

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_get_client_lazy_and_cached(self, mock_config):
        mock_config.get.return_value = '{"type": "service_account", "project_id": "test"}'
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer

        vs = GeminiVoiceSynthesizer()

        mock_tts_client = Mock()
        mock_creds = Mock()

        # Patch the lazy imports that _get_client performs
        with patch.dict('sys.modules', {
            'google.cloud.texttospeech': MagicMock(TextToSpeechClient=Mock(return_value=mock_tts_client)),
            'google.oauth2': MagicMock(),
            'google.oauth2.service_account': MagicMock(Credentials=MagicMock(from_service_account_info=Mock(return_value=mock_creds))),
        }):
            # Reset cached client
            vs._client = None

            client1 = vs._get_client()
            client2 = vs._get_client()
            assert client1 is client2
            assert client1 is mock_tts_client

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_get_client_handles_dict_config(self, mock_config):
        mock_config.get.return_value = {"type": "service_account", "project_id": "test"}
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer

        vs = GeminiVoiceSynthesizer()
        mock_tts_client = Mock()
        mock_creds = Mock()

        with patch.dict('sys.modules', {
            'google.cloud.texttospeech': MagicMock(TextToSpeechClient=Mock(return_value=mock_tts_client)),
            'google.oauth2': MagicMock(),
            'google.oauth2.service_account': MagicMock(Credentials=MagicMock(from_service_account_info=Mock(return_value=mock_creds))),
        }):
            vs._client = None
            client = vs._get_client()
            assert client is mock_tts_client

    # --- segmentation ---

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_next_segment_splits_on_sentence_boundary(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        segment, remainder = vs._next_segment("Hello there. How are you")
        assert segment == "Hello there. "
        assert remainder == "How are you"

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_next_segment_not_ready_without_boundary(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        segment, remainder = vs._next_segment("Hello ther")
        assert segment is None
        assert remainder == "Hello ther"

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_next_segment_force_flushes_remainder(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        segment, remainder = vs._next_segment("trailing words", force=True)
        assert segment == "trailing words"
        assert remainder == ""

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_next_segment_caps_runaway_buffer(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        long_buffer = "word " * 60  # 300 chars, no sentence terminator
        segment, remainder = vs._next_segment(long_buffer)
        assert segment is not None
        assert len(segment) <= vs.MAX_SEGMENT_CHARS
        assert segment + remainder == long_buffer

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_segments_yields_sentences_in_order(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        for chunk in ["This is one. ", "And t", "wo. ", "Tail"]:
            vs.text_queue.put(chunk)
        vs.text_queue.put(None)

        assert list(vs._segments()) == ["This is one. ", "And two. ", "Tail"]

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_segments_single_segment_when_no_boundary(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.text_queue.put("just a few words")
        vs.text_queue.put(None)

        assert list(vs._segments()) == ["just a few words"]

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_segments_empty_when_only_sentinel(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.text_queue.put(None)
        assert list(vs._segments()) == []

    # --- per-segment synthesis ---

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_synthesize_segment_scales_audio_by_output_gain(self, mock_config):
        import struct
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.OUTPUT_GAIN = 0.5

        loud = struct.pack('<hh', 1000, -2000)
        client = Mock()
        client.streaming_synthesize.return_value = [Mock(audio_content=loud)]

        with patch.object(vs, '_build_streaming_config', return_value=Mock()), \
             patch.object(vs, '_build_input_request', return_value=Mock()):
            vs._synthesize_segment(client, "Hello.")

        assert vs.audio_queue.get_nowait() == struct.pack('<hh', 500, -1000)

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_apply_gain_carries_odd_byte_across_chunks(self, mock_config):
        import struct
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.OUTPUT_GAIN = 0.5
        vs._gain_leftover = b""

        # A sample split across two chunks must still be scaled, not dropped.
        low, high = struct.pack('<h', 1000)[:1], struct.pack('<h', 1000)[1:]
        assert vs._apply_gain(low) == b""          # half a sample held back
        assert vs._apply_gain(high) == struct.pack('<h', 500)
        assert vs._gain_leftover == b""

    # --- run synthesis (orchestration) ---

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_run_synthesis_synthesizes_each_segment_then_sentinel(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.OUTPUT_GAIN = 1.0  # focus on segment sequencing, not gain scaling
        vs.text_queue.put("One. ")
        vs.text_queue.put("Two.")
        vs.text_queue.put(None)

        client = Mock()
        client.streaming_synthesize.side_effect = lambda gen: [Mock(audio_content=b"x")]

        with patch.object(vs, '_get_client', return_value=client), \
             patch.object(vs, '_build_streaming_config', return_value=Mock()), \
             patch.object(vs, '_build_input_request', return_value=Mock()):
            vs._run_synthesis()

        assert client.streaming_synthesize.call_count == 2
        chunks = []
        while not vs.audio_queue.empty():
            chunks.append(vs.audio_queue.get_nowait())
        assert chunks == [b"x", b"x", None]

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_run_synthesis_no_text_skips_client(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.text_queue.put(None)

        with patch.object(vs, '_get_client') as get_client:
            vs._run_synthesis()

        get_client.assert_not_called()
        assert vs.audio_queue.get_nowait() is None

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_run_synthesis_queues_none_on_error(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.text_queue.put("Hello.")
        vs.text_queue.put(None)

        client = Mock()
        client.streaming_synthesize.side_effect = RuntimeError("API error")

        with patch.object(vs, '_get_client', return_value=client), \
             patch.object(vs, '_build_streaming_config', return_value=Mock()), \
             patch.object(vs, '_build_input_request', return_value=Mock()):
            vs._run_synthesis()

        # Drain to the sentinel.
        last = None
        while not vs.audio_queue.empty():
            last = vs.audio_queue.get_nowait()
        assert last is None

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_run_synthesis_stops_on_close(self, mock_config):
        from interfaces.voice.tts.GeminiVoiceSynthesizer import GeminiVoiceSynthesizer
        vs = GeminiVoiceSynthesizer()
        vs.text_queue.put("One. ")
        vs.text_queue.put(None)
        vs.is_closed = True

        client = Mock()
        client.streaming_synthesize.return_value = [Mock(audio_content=b"x")]

        with patch.object(vs, '_get_client', return_value=client), \
             patch.object(vs, '_build_streaming_config', return_value=Mock()), \
             patch.object(vs, '_build_input_request', return_value=Mock()):
            vs._run_synthesis()

        client.streaming_synthesize.assert_not_called()
        assert vs.audio_queue.get_nowait() is None
