import time

import pytest

from assistant.AssistantRequest import RequestContext
from assistant.RequestProcessor import RequestData


def _make_request_data(args=None, user_agent='', json_body=None):
    headers = {'User-Agent': user_agent}
    return RequestData('GET', 'http://localhost/stream', args or {}, headers, json_body=json_body)


@pytest.mark.unit
class TestRequestContext:

    def test_android_client_defaults(self, monkeypatch):
        android_location = {}
        monkeypatch.setattr("assistant.AssistantRequest.config.get",
                            lambda key, default=None: android_location.get(key, default))
        rd = _make_request_data(user_agent='okhttp/4.12.0')
        ctx = RequestContext.from_request_data(rd)
        assert ctx.source == "VoiceToVoice"
        assert ctx.location is None
        assert ctx.user is None
        assert not ctx.user

        android_location["clients.android_location"] = "Kitchen"
        assert RequestContext.from_request_data(rd).location == "Kitchen"

    def test_ios_client_defaults(self):
        rd = _make_request_data(user_agent='Floxy/1 CFNetwork/1568.200.51')
        ctx = RequestContext.from_request_data(rd)
        assert ctx.source == "VoiceToVoice"
        assert ctx.location is None
        assert ctx.user is None
        assert not ctx.user

    def test_unknown_client_defaults(self):
        rd = _make_request_data(user_agent='python-requests/2.31.0')
        ctx = RequestContext.from_request_data(rd)
        assert ctx.source == "TextChat"
        assert ctx.location is None
        assert ctx.user is None
        assert not ctx.user

    def test_explicit_params_override_defaults(self):
        rd = _make_request_data(
            args={'source': 'CustomSource', 'location': 'Bedroom', 'user': 'Alice'},
            user_agent='okhttp/4.12.0',
        )
        ctx = RequestContext.from_request_data(rd)
        assert ctx.source == "CustomSource"
        assert ctx.location == "Bedroom"
        assert ctx.user == "Alice"
        assert ctx.user

    def test_user_set_when_provided(self):
        rd = _make_request_data(args={'user': 'Bob'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.user == "Bob"

    def test_user_none_when_absent(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.user is None

    def test_partial_explicit_params(self):
        rd = _make_request_data(args={'source': 'VoiceToVoice'}, user_agent='python-requests/2.31.0')
        ctx = RequestContext.from_request_data(rd)
        assert ctx.source == "VoiceToVoice"
        assert ctx.location is None
        assert ctx.user is None
        assert not ctx.user

    def test_user_input_extracted(self):
        rd = _make_request_data(args={'query': 'hello world'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.user_input == "hello world"

    def test_user_input_none_when_absent(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.user_input is None

    def test_text_only_true(self):
        rd = _make_request_data(args={'text': '1'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.text_only is True

    def test_text_only_false(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.text_only is False

    def test_server_sound_true(self):
        rd = _make_request_data(args={'server_sound': '1'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.server_sound is True

    def test_server_sound_false(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.server_sound is False

    def test_ai_override_extracted(self):
        rd = _make_request_data(args={'ai': 'fast'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.ai_override == "fast"

    def test_ai_override_none_when_absent(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.ai_override is None

    def test_test_true(self):
        rd = _make_request_data(args={'test': '1'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.test is True

    def test_test_false(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.test is False

    def test_display_name_with_user(self):
        ctx = RequestContext(user="Alice")
        assert ctx.display_name == "Alice"

    def test_display_name_without_user(self):
        ctx = RequestContext()
        assert ctx.display_name == "User"

    def test_all_fields_from_request(self):
        rd = _make_request_data(args={
            'query': 'what is the weather',
            'text': '1',
            'server_sound': '1',
            'ai': 'smart',
            'test': '1',
            'user': 'Bob',
            'source': 'Telegram',
            'location': 'Office',
        })
        ctx = RequestContext.from_request_data(rd)
        assert ctx.user_input == "what is the weather"
        assert ctx.text_only is True
        assert ctx.server_sound is True
        assert ctx.ai_override == "smart"
        assert ctx.test is True
        assert ctx.user == "Bob"
        assert ctx.source == "Telegram"
        assert ctx.location == "Office"
        assert ctx.display_name == "Bob"

    def test_engine_extracted_from_request(self):
        # Local outposts send engine=whisper/scribe; the server uses it to skip the
        # sentence-completeness hold (their transcripts are already final).
        rd = _make_request_data(args={'engine': 'whisper'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.engine == "whisper"

    def test_engine_none_when_absent(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.engine is None

    def test_lang_extracted_from_request(self):
        # DialogManager.select_voice routes the request to the Swedish synth when
        # ctx.lang starts with "sv", so this field must round-trip from the URL.
        rd = _make_request_data(args={'lang': 'sv-SE'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.lang == "sv-SE"

    def test_lang_none_when_absent(self):
        # Missing lang must be None so DialogManager._voice_for_lang falls back
        # to the configured language instead of dispatching to Swedish blindly.
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.lang is None

    def test_images_none_when_no_json_body(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.images is None

    def test_images_none_when_json_body_has_no_images(self):
        rd = _make_request_data(args={}, json_body={"query": "hello"})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.images is None

    def test_images_extracted_from_json_body(self):
        images = [{"data": "abc123", "media_type": "image/jpeg"}]
        rd = _make_request_data(args={}, json_body={"images": images})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.images == images
        assert len(ctx.images) == 1
        assert ctx.images[0]["data"] == "abc123"

    def test_images_none_when_empty_list(self):
        rd = _make_request_data(args={}, json_body={"images": []})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.images is None

    def test_speech_end_epoch_reconstructed_from_stt_ms(self):
        # stt_ms is the outpost's speech-end -> transcript-send latency; arrival time minus
        # it recovers when the user actually stopped speaking, which anchors the
        # natural-timing wait in RequestProcessor.
        before = time.time()
        rd = _make_request_data(args={'stt_ms': '900'})
        ctx = RequestContext.from_request_data(rd)
        after = time.time()
        assert ctx.speech_end_epoch is not None
        assert before - 0.9 <= ctx.speech_end_epoch <= after - 0.9

    def test_speech_end_epoch_none_when_stt_ms_absent(self):
        rd = _make_request_data(args={})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.speech_end_epoch is None

    def test_speech_end_epoch_none_when_stt_ms_malformed(self):
        rd = _make_request_data(args={'stt_ms': 'garbage'})
        ctx = RequestContext.from_request_data(rd)
        assert ctx.speech_end_epoch is None

    def test_negative_stt_ms_clamped_to_now(self):
        # A negative stt_ms would put speech_end in the FUTURE, and the natural-timing
        # wait sleeps uncapped until that moment — one bad client value would stall the
        # request worker. Clamped to "speech just ended".
        before = time.time()
        rd = _make_request_data(args={'stt_ms': '-3600000'})
        ctx = RequestContext.from_request_data(rd)
        after = time.time()
        assert ctx.speech_end_epoch is not None
        assert before <= ctx.speech_end_epoch <= after

    def test_large_positive_stt_ms_not_clamped(self):
        # Only negative values are clamped: a large positive stt_ms is harmless (it
        # merely shortens the natural-timing wait to zero), so a symmetric clamp
        # that "fixed" it too would erase real latency data.
        before = time.time()
        rd = _make_request_data(args={'stt_ms': '3600000'})
        ctx = RequestContext.from_request_data(rd)
        after = time.time()
        assert ctx.speech_end_epoch is not None
        assert before - 3600 <= ctx.speech_end_epoch <= after - 3600
