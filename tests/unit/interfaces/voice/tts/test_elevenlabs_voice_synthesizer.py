import base64
import json
from unittest.mock import Mock, patch

import pytest

MODULE = 'interfaces.voice.tts.ElevenLabsVoiceSynthesizer'


@pytest.fixture
def synthesizer():
    with patch(f'{MODULE}.config') as mock_config:
        mock_config.get_api_key.return_value = "test-key"
        from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer
        yield ElevenLabsVoiceSynthesizer()


def _record_sends(vs) -> list[dict]:
    sent = []
    vs.ws = Mock()
    vs.ws.send = lambda msg: sent.append(json.loads(msg))
    return sent


def _feed(vs, *chunks) -> list[dict]:
    sent = _record_sends(vs)
    vs.is_connected = True
    for chunk in chunks:
        vs.text_queue.put(chunk)
    vs.text_queue.put(None)
    vs._feed_text_thread()
    return sent


class TestVoiceChoice:
    def test_voice_id_day(self, synthesizer):
        from assistant.prompt.TimeOfDay import TimeOfDay
        with patch(f'{MODULE}.TimeOfDay') as mock_tod:
            mock_tod.get_current.return_value = TimeOfDay.DAY
            mock_tod.DAY = TimeOfDay.DAY
            assert synthesizer.voice_id() == "QtY3JBOUKEB5xzrRfOKc"  # Maisie

    def test_voice_id_night(self, synthesizer):
        from assistant.prompt.TimeOfDay import TimeOfDay
        with patch(f'{MODULE}.TimeOfDay') as mock_tod:
            mock_tod.get_current.return_value = TimeOfDay.NIGHT
            mock_tod.DAY = TimeOfDay.DAY
            assert synthesizer.voice_id() == "geVuXdUpU0lgUukWJCUE"  # Shannon


class TestModelChoice:
    def test_model_is_the_tts_elevenlabs_model_config_key(self):
        """Contract with config.yaml: `tts.elevenlabs.model` picks the model."""
        with patch(f'{MODULE}.config') as mock_config, \
                patch('interfaces.voice.tts.VoiceSynthesizer.config') as base_config:
            mock_config.get_api_key.return_value = "k"
            base_config.get.return_value = {"elevenlabs": {"model": "eleven_flash_v2_5"}}
            from interfaces.voice.tts.ElevenLabsVoiceSynthesizer import ElevenLabsVoiceSynthesizer
            assert ElevenLabsVoiceSynthesizer().model_id == "eleven_flash_v2_5"

    def test_cache_variant_is_the_model(self, synthesizer):
        synthesizer.model_id = "eleven_flash_v2_5"
        assert synthesizer.cache_variant() == "eleven_flash_v2_5"

    def test_default_model_streams_over_the_dialogue_socket(self, synthesizer):
        """The v4 family is rejected by the text-to-speech stream-input socket, so the
        default must route through text-to-dialogue."""
        assert synthesizer.uses_dialogue_socket
        assert "/v1/text-to-dialogue/stream-input" in synthesizer._get_ws_url()
        assert f"model_id={synthesizer.model_id}" in synthesizer._get_ws_url()

    def test_legacy_model_streams_over_the_voice_stream_input_socket(self, synthesizer):
        synthesizer.model_id = "eleven_flash_v2_5"
        with patch.object(type(synthesizer), 'voice_id', staticmethod(lambda: "voice1")):
            url = synthesizer._get_ws_url()
        assert not synthesizer.uses_dialogue_socket
        assert "/v1/text-to-speech/voice1/stream-input" in url
        assert "model_id=eleven_flash_v2_5" in url


class TestDialogueProtocol:
    def test_on_open_registers_the_current_voice_and_the_key(self, synthesizer):
        mock_ws = Mock()
        with patch('threading.Thread'), \
                patch.object(type(synthesizer), 'voice_id', staticmethod(lambda: "voice1")):
            synthesizer._on_open(mock_ws)

        assert synthesizer.is_connected is True
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent == {"voices": ["voice1"], "xi_api_key": "test-key"}
        assert synthesizer.session_voice_id == "voice1"

    def test_text_goes_as_inputs_for_the_registered_voice(self, synthesizer):
        synthesizer.session_voice_id = "voice1"
        sent = _feed(synthesizer, "Hello world")
        assert sent[0] == {"inputs": [{"text": "Hello world", "voice_id": "voice1", "new_turn": False}]}

    def test_end_of_text_closes_the_socket(self, synthesizer):
        sent = _feed(synthesizer, "Hello")
        assert sent[-1] == {"close_socket": True}
        assert sum(1 for m in sent if "inputs" in m) == 1

    def test_whitespace_only_chunks_are_not_sent(self, synthesizer):
        sent = _feed(synthesizer, "   ", "", "Hello")
        assert [m for m in sent if "inputs" in m] == [
            {"inputs": [{"text": "Hello", "voice_id": "", "new_turn": False}]}]

    def test_is_final_ends_the_audio_stream(self, synthesizer):
        with patch('time.sleep'):
            synthesizer._on_message(None, json.dumps({"is_final": True, "audio": None}))
        assert synthesizer.audio_queue.get_nowait() is None


class TestLegacyProtocol:
    @pytest.fixture(autouse=True)
    def legacy_model(self, synthesizer):
        synthesizer.model_id = "eleven_flash_v2_5"

    def test_on_open_sends_bos_config(self, synthesizer):
        mock_ws = Mock()
        with patch('threading.Thread'):
            synthesizer._on_open(mock_ws)

        assert synthesizer.is_connected is True
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent['bos'] is True
        assert sent['xi_api_key'] == "test-key"

    def test_text_frames_trigger_generation(self, synthesizer):
        sent = _feed(synthesizer, "Hello world")
        assert sent[0]['text'] == "Hello world"
        assert sent[0]['try_trigger_generation'] is True
        assert sent[0]['bos'] is False

    def test_end_of_text_sends_eos(self, synthesizer):
        sent = _feed(synthesizer, "Hello")
        assert sent[-1] == {"text": "", "eos": True}

    def test_is_final_ends_the_audio_stream(self, synthesizer):
        with patch('time.sleep'):
            synthesizer._on_message(None, json.dumps({"audio": None, "isFinal": True}))
        assert synthesizer.audio_queue.get_nowait() is None


class TestAudioHandling:
    def test_on_message_queues_decoded_audio(self, synthesizer):
        audio = b"test audio bytes"
        synthesizer._on_message(None, json.dumps({"audio": base64.b64encode(audio).decode()}))
        assert synthesizer.audio_queue.get_nowait() == audio

    def test_on_error_queues_none(self, synthesizer):
        synthesizer._on_error(None, RuntimeError("test"))
        assert synthesizer.audio_queue.get_nowait() is None

    def test_feed_text_thread_sends_nothing_once_closed(self, synthesizer):
        synthesizer.is_closed = True
        sent = _feed(synthesizer, "Hello")
        assert sent == []
