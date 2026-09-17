import json
import sys
import types
from unittest.mock import patch, MagicMock
import pytest

from interfaces.telegram.GoogleCloudSTT import GoogleCloudSTT


@pytest.fixture(autouse=True)
def fake_google_modules():
    """Stub the heavy google.cloud.speech import so these tests stay fast.

    Importing the real library pulls in the full gRPC/protobuf stack (~0.6s on
    first import), which dominates the runtime of these otherwise-trivial tests.
    """
    speech = MagicMock(name="google.cloud.speech")
    service_account = MagicMock(name="google.oauth2.service_account")

    cloud_pkg = types.ModuleType("google.cloud")
    cloud_pkg.speech = speech
    oauth2_pkg = types.ModuleType("google.oauth2")
    oauth2_pkg.service_account = service_account
    google_pkg = types.ModuleType("google")
    google_pkg.cloud = cloud_pkg
    google_pkg.oauth2 = oauth2_pkg

    modules = {
        "google": google_pkg,
        "google.cloud": cloud_pkg,
        "google.cloud.speech": speech,
        "google.oauth2": oauth2_pkg,
        "google.oauth2.service_account": service_account,
    }
    with patch.dict(sys.modules, modules):
        yield


@pytest.fixture(autouse=True)
def reset_singleton():
    GoogleCloudSTT.reset_instance()
    yield
    GoogleCloudSTT.reset_instance()


class TestGoogleCloudSTTInit:
    @patch('interfaces.telegram.GoogleCloudSTT.config')
    def test_init_with_dict_credentials(self, mock_config):
        mock_config.get.return_value = {"type": "service_account", "project_id": "test"}
        mock_client = MagicMock()
        mock_creds = MagicMock()

        with patch('google.cloud.speech.SpeechClient', return_value=mock_client), \
             patch('google.oauth2.service_account.Credentials.from_service_account_info',
                   return_value=mock_creds):
            stt = GoogleCloudSTT()

        assert stt._client is mock_client

    @patch('interfaces.telegram.GoogleCloudSTT.config')
    def test_init_with_json_string_credentials(self, mock_config):
        creds_dict = {"type": "service_account", "project_id": "test"}
        mock_config.get.return_value = json.dumps(creds_dict)
        mock_client = MagicMock()

        with patch('google.cloud.speech.SpeechClient', return_value=mock_client), \
             patch('google.oauth2.service_account.Credentials.from_service_account_info') as mock_from_info:
            mock_from_info.return_value = MagicMock()
            stt = GoogleCloudSTT()

        mock_from_info.assert_called_once_with(creds_dict)
        assert stt._client is mock_client

    @patch('interfaces.telegram.GoogleCloudSTT.config')
    def test_init_failure_sets_client_none(self, mock_config):
        mock_config.get.side_effect = Exception("no config")
        stt = GoogleCloudSTT()
        assert stt._client is None

    @patch('interfaces.telegram.GoogleCloudSTT.config')
    def test_singleton_returns_same_instance(self, mock_config):
        mock_config.get.side_effect = Exception("no config")
        a = GoogleCloudSTT.get_instance()
        b = GoogleCloudSTT.get_instance()
        assert a is b


class TestGoogleCloudSTTTranscribe:
    def _make_stt_with_mock_client(self):
        stt = GoogleCloudSTT.__new__(GoogleCloudSTT)
        stt._client = MagicMock()
        return stt

    def test_transcribe_no_client_returns_empty(self):
        stt = GoogleCloudSTT.__new__(GoogleCloudSTT)
        stt._client = None
        assert stt.transcribe(b"audio") == ""

    def test_transcribe_joins_multiple_results(self):
        stt = self._make_stt_with_mock_client()

        alt1 = MagicMock()
        alt1.transcript = "hello"
        result1 = MagicMock()
        result1.alternatives = [alt1]

        alt2 = MagicMock()
        alt2.transcript = "world"
        result2 = MagicMock()
        result2.alternatives = [alt2]

        response = MagicMock()
        response.results = [result1, result2]
        stt._client.recognize.return_value = response

        with patch('google.cloud.speech.RecognitionAudio'), \
             patch('google.cloud.speech.RecognitionConfig'):
            assert stt.transcribe(b"audio") == "hello world"

    def test_transcribe_skips_results_without_alternatives(self):
        stt = self._make_stt_with_mock_client()

        alt = MagicMock()
        alt.transcript = "only this"
        result_with = MagicMock()
        result_with.alternatives = [alt]

        result_without = MagicMock()
        result_without.alternatives = []

        response = MagicMock()
        response.results = [result_without, result_with]
        stt._client.recognize.return_value = response

        with patch('google.cloud.speech.RecognitionAudio'), \
             patch('google.cloud.speech.RecognitionConfig'):
            assert stt.transcribe(b"audio") == "only this"

    def test_transcribe_no_speech_returns_empty(self):
        stt = self._make_stt_with_mock_client()

        response = MagicMock()
        response.results = []
        stt._client.recognize.return_value = response

        with patch('google.cloud.speech.RecognitionAudio'), \
             patch('google.cloud.speech.RecognitionConfig'):
            assert stt.transcribe(b"audio") == ""

    def test_transcribe_exception_returns_empty(self):
        stt = self._make_stt_with_mock_client()
        stt._client.recognize.side_effect = Exception("API error")

        with patch('google.cloud.speech.RecognitionAudio'), \
             patch('google.cloud.speech.RecognitionConfig'):
            assert stt.transcribe(b"audio") == ""
