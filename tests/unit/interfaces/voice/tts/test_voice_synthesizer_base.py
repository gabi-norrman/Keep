from unittest.mock import patch

from common.RequestKilledException import RequestKilledException
from interfaces.voice.tts.VoiceSynthesizer import VoiceSynthesizer


class ConcreteVoiceSynthesizer(VoiceSynthesizer):
    def name_replacements(self):
        return [("siobhan", "shivawn"), ("sam", "sam")]

    def start(self, llm_text_stream):
        self.llm_text_stream = llm_text_stream
        return self._audio_generator()

    @staticmethod
    def voice_id():
        return "test-voice-id"

    def _join_threads(self):
        pass


class NoReplacementVoiceSynthesizer(VoiceSynthesizer):
    def name_replacements(self):
        return []

    def start(self, llm_text_stream):
        self.llm_text_stream = llm_text_stream
        return self._audio_generator()

    @staticmethod
    def voice_id():
        return "no-replace"

    def _join_threads(self):
        pass


class TagAwareVoiceSynthesizer(VoiceSynthesizer):
    SUPPORTS_EMOTION_TAGS = True
    def name_replacements(self):
        return []

    def start(self, llm_text_stream):
        self.llm_text_stream = llm_text_stream
        return self._audio_generator()

    @staticmethod
    def voice_id():
        return "tag-aware"

    def _join_threads(self):
        pass


class TestVoiceSynthesizerBase:
    def test_audio_generator_yields_until_none_sentinel(self):
        vs = ConcreteVoiceSynthesizer()
        vs.audio_queue.put(b"chunk1")
        vs.audio_queue.put(b"chunk2")
        vs.audio_queue.put(None)

        result = list(vs._audio_generator())
        assert result == [b"chunk1", b"chunk2"]

    def test_feed_text_to_queue_name_replacement(self):
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello siobhan, how are you?"])

        vs._feed_text_to_queue()

        items = []
        while not vs.text_queue.empty():
            item = vs.text_queue.get_nowait()
            if item is None:
                break
            items.append(item)
        combined = "".join(items)
        assert "shivawn" in combined
        assert "siobhan" not in combined.lower()

    def test_feed_text_strips_comma_before_same_name_replacement(self):
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello, sam, how are you?"])

        vs._feed_text_to_queue()

        items = []
        while not vs.text_queue.empty():
            item = vs.text_queue.get_nowait()
            if item is None:
                break
            items.append(item)
        combined = "".join(items)
        assert ", sam" not in combined.lower()
        assert "sam" in combined.lower()

    def test_feed_text_sends_none_sentinel(self):
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["hello"])

        vs._feed_text_to_queue()

        items = []
        while True:
            item = vs.text_queue.get_nowait()
            items.append(item)
            if item is None:
                break
        assert items[-1] is None

    def test_feed_text_applies_custom_preprocessor(self):
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["hello–world"])

        vs._feed_text_to_queue(text_preprocessor=lambda c: c.replace("–", ","))

        items = []
        while not vs.text_queue.empty():
            item = vs.text_queue.get_nowait()
            if item is None:
                break
            items.append(item)
        combined = "".join(items)
        assert "–" not in combined
        assert "," in combined

    def test_feed_text_lookahead_buffering(self):
        vs = ConcreteVoiceSynthesizer()
        # "sio" could be start of "siobhan", so it should be held back
        vs.llm_text_stream = iter(["sio", "bhan is here"])

        vs._feed_text_to_queue()

        items = []
        while not vs.text_queue.empty():
            item = vs.text_queue.get_nowait()
            if item is None:
                break
            items.append(item)
        combined = "".join(items)
        assert "shivawn" in combined

class TestVoiceSynthesizerNameEdgeCases:

    @staticmethod
    def _collect_text(vs):
        items = []
        while not vs.text_queue.empty():
            item = vs.text_queue.get_nowait()
            if item is None:
                break
            items.append(item)
        return "".join(items)

    def test_name_at_end_of_stream(self):
        """Last chunk is 'siobhan' → flushed with replacement at stream end"""
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello ", "siobhan"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "shivawn" in combined
        assert "siobhan" not in combined.lower()

    def test_name_split_across_three_chunks(self):
        """'si', 'ob', 'han is here' → hold-back buffers reassemble name"""
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["si", "ob", "han is here"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "shivawn" in combined
        assert "siobhan" not in combined.lower()

    def test_comma_prefixed_name_across_chunks(self):
        """', sio' + 'bhan' → comma stripping + replacement applied"""
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello", ", sio", "bhan!"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "shivawn" in combined
        assert ", shivawn" not in combined

    def test_no_replacements_passthrough(self):
        """No name replacements → text unchanged"""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello siobhan, how are you?"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert combined == "Hello siobhan, how are you?"

    def test_case_insensitive(self):
        """'SIOBHAN' and 'Siobhan' both replaced"""
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["SIOBHAN and Siobhan"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "siobhan" not in combined.lower()
        # Should contain shivawn twice (case insensitive replacement)
        assert combined.lower().count("shivawn") == 2

    def test_name_prefix_that_doesnt_complete(self):
        """'si' + 'lver' → 'silver' passes through, no replacement"""
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["si", "lver is here"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "silver" in combined
        assert "shivawn" not in combined

    def test_empty_chunks_in_stream(self):
        """Empty strings between chunks → no effect"""
        vs = ConcreteVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello", "", "", " world", ""])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "Hello world" in combined

    def test_is_closed_stops_processing(self):
        """is_closed=True mid-stream → remaining chunks skipped"""
        vs = ConcreteVoiceSynthesizer()

        def closing_stream():
            yield "Hello "
            vs.is_closed = True
            yield "siobhan"
            yield " should not appear"

        vs.llm_text_stream = closing_stream()
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "should not appear" not in combined


class TestVoiceSynthesizerEmotionTags:

    @staticmethod
    def _collect_text(vs):
        items = []
        while not vs.text_queue.empty():
            item = vs.text_queue.get_nowait()
            if item is None:
                break
            items.append(item)
        return "".join(items)

    def test_tags_stripped_when_unsupported(self):
        """Default SUPPORTS_EMOTION_TAGS=False → bracket tags removed"""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = iter(["[laughing] no way, that worked?"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "[laughing]" not in combined
        assert "no way, that worked?" in combined

    def test_tags_kept_when_supported(self):
        """SUPPORTS_EMOTION_TAGS=True → tags pass through untouched"""
        vs = TagAwareVoiceSynthesizer()
        vs.llm_text_stream = iter(["[laughing] no way!"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "[laughing]" in combined

    def test_tag_split_across_chunks_stripped(self):
        """A tag split over multiple chunks is held back, then stripped whole"""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = iter(["Hello [lau", "ghing] there"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "[" not in combined and "]" not in combined
        assert "lau" not in combined and "ghing" not in combined
        assert "Hello" in combined
        assert "there" in combined

    def test_unclosed_tag_at_stream_end(self):
        """A never-closed '[' flushes at end (no infinite hold)"""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = iter(["Done [unclosed"])
        vs._feed_text_to_queue()
        combined = self._collect_text(vs)
        assert "Done" in combined


class TestVoiceSynthesizerStreamTermination:
    """Stream-termination paths in _feed_text_to_queue.

    When a speculative request is superseded mid-synthesis, the LLM stream raises
    RequestKilledException (or GeneratorExit on generator teardown). The catch-all
    used to log these as 'Error feeding LLM response: ' with a blank cause, which
    looked like a real failure. They are now caught explicitly and traced, and the
    text queue is still terminated with a None sentinel so the audio consumer
    unblocks cleanly. Genuine errors keep going through the catch-all, which now
    includes the exception type so the log line is never blank."""

    @staticmethod
    def _drain_until_sentinel(vs, max_items=20):
        items = []
        for _ in range(max_items):
            item = vs.text_queue.get_nowait()
            items.append(item)
            if item is None:
                return items
        raise AssertionError("No None sentinel observed within max_items")

    @staticmethod
    def _raising_stream(prefix_chunks, exc):
        for chunk in prefix_chunks:
            yield chunk
        raise exc

    def test_request_killed_mid_stream_is_traced_not_errored(self):
        """RequestKilledException → trace log, sentinel queued, no error log."""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = self._raising_stream(
            ["partial reply "], RequestKilledException("superseded")
        )

        with patch("common.logging.error") as mock_error, \
             patch("common.logging.trace") as mock_trace:
            vs._feed_text_to_queue()

        items = self._drain_until_sentinel(vs)
        assert items[-1] is None
        assert "partial reply " in "".join(s for s in items if s is not None)
        mock_error.assert_not_called()
        assert any("killed mid-synthesis" in str(c) for c in mock_trace.call_args_list)

    def test_generator_exit_is_traced_not_errored(self):
        """GeneratorExit (e.g. generator.close() during teardown) → traced, sentinel queued."""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = self._raising_stream([], GeneratorExit())

        with patch("common.logging.error") as mock_error, \
             patch("common.logging.trace") as mock_trace:
            vs._feed_text_to_queue()

        assert vs.text_queue.get_nowait() is None
        mock_error.assert_not_called()
        assert any("killed mid-synthesis" in str(c) for c in mock_trace.call_args_list)

    def test_generic_exception_logs_with_type_name(self):
        """Genuine errors keep going through the catch-all, but the log now
        includes the exception type so the failure isn't blank."""
        vs = NoReplacementVoiceSynthesizer()
        vs.llm_text_stream = self._raising_stream([], RuntimeError("boom"))

        with patch("common.logging.error") as mock_error:
            vs._feed_text_to_queue()

        assert vs.text_queue.get_nowait() is None
        mock_error.assert_called_once()
        message = mock_error.call_args.args[0]
        assert "RuntimeError" in message
        assert "boom" in message

    def test_blank_exception_still_identifiable_by_type(self):
        """The original bug: an exception with an empty str() produced a blank
        'Error feeding LLM response: ' line. The fix prefixes the type name so
        the log line is still actionable."""
        vs = NoReplacementVoiceSynthesizer()

        class SilentError(Exception):
            pass

        vs.llm_text_stream = self._raising_stream([], SilentError())

        with patch("common.logging.error") as mock_error:
            vs._feed_text_to_queue()

        mock_error.assert_called_once()
        assert "SilentError" in mock_error.call_args.args[0]


class TestVoiceSynthesizerRequestLanguage:
    """Per-request locale plumbing on the base class.

    DialogManager.select_voice calls set_request_language(ctx.lang) and the
    synth then reads effective_language()/effective_language_code() when
    building the TTS request. This is what makes a Swedish query speak Swedish
    even when the global config is en-US (and vice-versa). Pinning the base
    methods here so a backend-specific test isn't the only thing guarding the
    contract."""

    def test_configured_language_code_strips_region_subtag(self):
        with patch("common.Language.config") as mock_config:
            mock_config.get.return_value = "pt-BR"
            assert VoiceSynthesizer.configured_language() == "pt-BR"
            assert VoiceSynthesizer.configured_language_code() == "pt"

    def test_configured_language_default_when_unset(self):
        """Missing language config falls through to en-US (used as the default
        in common.Language.language_code as well — keep both honest)."""
        with patch("common.Language.config") as mock_config:
            mock_config.get.side_effect = lambda key, default=None: default
            assert VoiceSynthesizer.configured_language() == "en-US"

    def test_configured_languages_scalar_normalizes_to_list(self):
        with patch("common.Language.config") as mock_config:
            mock_config.get.return_value = "en-US"
            assert VoiceSynthesizer.configured_languages() == ["en-US"]
            assert VoiceSynthesizer.configured_language() == "en-US"

    def test_configured_languages_list_keeps_order_primary_first(self):
        """A list 'language' lets the local outposts listen for several languages;
        the first is the primary that single-locale consumers (TTS, /outpost_settings)
        fall back to."""
        with patch("common.Language.config") as mock_config:
            mock_config.get.return_value = ["en-US", "sv-SE"]
            assert VoiceSynthesizer.configured_languages() == ["en-US", "sv-SE"]
            assert VoiceSynthesizer.configured_language() == "en-US"

    def test_configured_languages_empty_falls_back(self):
        with patch("common.Language.config") as mock_config:
            mock_config.get.return_value = []
            assert VoiceSynthesizer.configured_languages() == ["en-US"]

class ConfiguredVoiceSynthesizer(NoReplacementVoiceSynthesizer):
    CONFIG_SECTION = "engine"
    name_replacements = VoiceSynthesizer.name_replacements


class TestNameReplacementsFromConfig:
    @staticmethod
    def _replacements(tts):
        with patch('interfaces.voice.tts.VoiceSynthesizer.config') as mock_config:
            mock_config.get.side_effect = lambda key, default=None: tts if key == 'tts' else default
            return dict(ConfiguredVoiceSynthesizer().name_replacements())

    def test_names_map_to_themselves_lowercased(self):
        assert self._replacements({'names': ['Sam', 'alex']}) == {'sam': 'sam', 'alex': 'alex'}

    def test_pronunciation_replaces_the_plain_name(self):
        tts = {'names': ['siobhan'], 'name_pronunciations': {'Siobhan': 'shivawn'}}
        assert self._replacements(tts) == {'siobhan': 'shivawn'}

    def test_engine_pronunciation_overrides_the_shared_one(self):
        tts = {'name_pronunciations': {'siobhan': 'shivawn', 'sam': 'sahm'},
               'engine': {'name_pronunciations': {'siobhan': '<<ipa>>'}},
               'other': {'name_pronunciations': {'sam': 'ignored'}}}
        assert self._replacements(tts) == {'siobhan': '<<ipa>>', 'sam': 'sahm'}
