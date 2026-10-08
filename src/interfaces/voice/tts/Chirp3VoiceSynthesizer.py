from .GeminiVoiceSynthesizer import GeminiVoiceSynthesizer


class Chirp3VoiceSynthesizer(GeminiVoiceSynthesizer):
    """A Google Chirp 3 HD voice, talking after the first words of an answer.

    Speaks Keep's replies with the Chirp 3 HD voices of Google Cloud TTS, which start talking
    after the first words of an answer.

    Shares the Gemini synthesizer's streaming machinery (threads, queues, audio generator) but
    targets Chirp 3 HD, which streams truly incrementally: audio starts after the first words
    instead of after most of the text (as the Gemini TTS model does). Trade-off: Chirp 3 HD
    takes plain text only, so prompt-based tone steering is unavailable and emotion tags
    ([laughing] etc.) are stripped before synthesis (SUPPORTS_EMOTION_TAGS = False)."""

    SUPPORTS_EMOTION_TAGS = False

    CONFIG_SECTION = "chirp3"

    @property
    def name(self) -> str:
        return "Chirp 3 HD"

    @staticmethod
    def voice_id() -> str:
        return GeminiVoiceSynthesizer.configured_voice("chirp3", "Sulafat")

    def _chirp_voice_name(self) -> str:
        """Chirp 3 HD encodes the model in the voice name: <locale>-Chirp3-HD-<voice>."""
        return f"{self.effective_language()}-Chirp3-HD-{self.voice_id()}"

    def _build_streaming_config(self):
        from google.cloud import texttospeech

        # Chirp 3 HD selects the model via the voice name; no model_name field.
        # noinspection PyTypeChecker
        return texttospeech.StreamingSynthesizeRequest(
            streaming_config=texttospeech.StreamingSynthesizeConfig(
                voice=texttospeech.VoiceSelectionParams(
                    name=self._chirp_voice_name(),
                    language_code=self.effective_language(),
                )
            )
        )

    def _build_input_request(self, text: str, is_first: bool):
        from google.cloud import texttospeech

        # Chirp 3 HD streaming accepts plain text only - no prompt-based steering.
        # noinspection PyTypeChecker
        return texttospeech.StreamingSynthesizeRequest(
            input=texttospeech.StreamingSynthesisInput(text=text)
        )
