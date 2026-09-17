import json
import os
from typing import Optional, ClassVar

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

from common.logging import info, error, trace
from common.ConfigManager import config


class GoogleCloudSTT:
    """Transcribes Telegram voice notes with Google Cloud Speech-to-Text."""

    _instance: ClassVar[Optional['GoogleCloudSTT']] = None
    _client = None

    @classmethod
    def get_instance(cls) -> 'GoogleCloudSTT':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    def __init__(self):
        try:
            from google.cloud import speech
            from google.oauth2 import service_account

            service_account_info = config.get('google_service_account')
            if isinstance(service_account_info, str):
                service_account_info = json.loads(service_account_info)
            credentials = service_account.Credentials.from_service_account_info(service_account_info)
            self._client = speech.SpeechClient(credentials=credentials)
            info("GoogleCloudSTT initialized")
        except Exception as e:
            error(f"GoogleCloudSTT initialization failed: {e}")
            self._client = None

    def transcribe(self, audio_bytes: bytes) -> str:
        if not self._client:
            error("GoogleCloudSTT: client not initialized")
            return ""

        try:
            from google.cloud import speech

            audio = speech.RecognitionAudio(content=audio_bytes)
            recognition_config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
                sample_rate_hertz=48000,
                language_code="en-US",
            )

            response = self._client.recognize(config=recognition_config, audio=audio)

            transcript = " ".join(
                result.alternatives[0].transcript
                for result in response.results
                if result.alternatives
            )

            if transcript:
                info(f"GoogleCloudSTT transcription: {transcript}")
            else:
                trace("GoogleCloudSTT: no speech detected")

            return transcript
        except Exception as e:
            error(f"GoogleCloudSTT transcription error: {e}")
            return ""
