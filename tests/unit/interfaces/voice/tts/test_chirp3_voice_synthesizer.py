from unittest.mock import Mock, MagicMock, patch


class TestChirp3VoiceSynthesizer:
    @patch('common.Language.config')
    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_chirp_voice_name_encodes_locale_and_model(self, mock_gemini_config, mock_base_config):
        mock_base_config.get.return_value = "sv-SE"
        from interfaces.voice.tts.Chirp3VoiceSynthesizer import Chirp3VoiceSynthesizer
        vs = Chirp3VoiceSynthesizer()
        assert vs._chirp_voice_name() == "sv-SE-Chirp3-HD-Sulafat"

    @patch('common.Language.config')
    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_streaming_config_uses_chirp_voice_without_model_name(self, mock_gemini_config, mock_base_config):
        mock_base_config.get.return_value = "sv-SE"
        from interfaces.voice.tts.Chirp3VoiceSynthesizer import Chirp3VoiceSynthesizer

        mock_tts_module = MagicMock()
        captured = {}

        def capture_voice(**kw):
            captured.update(kw)
            return kw
        mock_tts_module.VoiceSelectionParams = Mock(side_effect=capture_voice)
        mock_tts_module.StreamingSynthesizeConfig = Mock(side_effect=lambda **kw: kw)
        mock_tts_module.StreamingSynthesizeRequest = Mock(side_effect=lambda **kw: kw)

        vs = Chirp3VoiceSynthesizer()
        with patch.dict('sys.modules', {'google.cloud.texttospeech': mock_tts_module}):
            vs._build_streaming_config()

        assert captured['name'] == "sv-SE-Chirp3-HD-Sulafat"
        assert captured['language_code'] == "sv-SE"
        assert 'model_name' not in captured  # Chirp 3 HD encodes the model in the voice name

    @patch('interfaces.voice.tts.GeminiVoiceSynthesizer.config')
    def test_input_request_is_text_only_no_prompt(self, mock_config):
        from interfaces.voice.tts.Chirp3VoiceSynthesizer import Chirp3VoiceSynthesizer

        mock_tts_module = MagicMock()
        captured = {}

        def capture_input(**kw):
            captured.update(kw)
            return kw
        mock_tts_module.StreamingSynthesisInput = Mock(side_effect=capture_input)
        mock_tts_module.StreamingSynthesizeRequest = Mock(side_effect=lambda **kw: kw)

        vs = Chirp3VoiceSynthesizer()
        with patch.dict('sys.modules', {'google.cloud.texttospeech': mock_tts_module}):
            # Even the first chunk must carry no prompt (Gemini-only feature).
            vs._build_input_request("Hej världen", is_first=True)

        assert captured['text'] == "Hej världen"
        assert 'prompt' not in captured
