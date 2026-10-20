"""Ds4LLM — the OpenAI-style backend for a local ds4-server, and the `ollama.local_only` switch
routing every tier to it by class name."""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from assistant.llm import parse_llm_config, tier_api_key_name, local_only_class
from assistant.llm.Ds4LLM import Ds4LLM


def _config(values: dict):
    """Patch the config both the factory and Ds4LLM read: known keys from *values*, the rest default."""
    def get(key, default=None):
        return values.get(key, default)
    return patch('assistant.llm.config.get', side_effect=get), patch('assistant.llm.Ds4LLM.config.get', side_effect=get)


def _chunk(content=None, reasoning=None, usage=None, choices=True):
    delta = SimpleNamespace(content=content)
    if reasoning is not None:
        delta.reasoning_content = reasoning
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)] if choices else [], usage=usage)


def _llm_with_stream(chunks, values=None) -> tuple[Ds4LLM, MagicMock]:
    p1, p2 = _config(values or {})
    with p1, p2:
        llm = Ds4LLM()
    llm.client = MagicMock()
    llm.client.chat.completions.create.return_value = iter(chunks)
    return llm, llm.client.chat.completions.create


class TestKeyless:
    def test_is_keyless_for_the_setup_code_like_ollama(self):
        # Contract with the integrations that run on a tier (Photos asks tier_api_key_name for the key it
        # needs): a local server needs none, so the answer is None, not an api_keys entry.
        p1, p2 = _config({'ollama.local_only': 'Ds4LLM'})
        with p1, p2:
            assert Ds4LLM().api_key_name() is None
            assert Ds4LLM().missing_api_key() is None
            assert tier_api_key_name('photo_indexing') is None

    def test_the_openai_client_still_gets_a_key_and_the_configured_server(self):
        # The OpenAI SDK refuses to construct without a key; the server ignores whatever it is.
        p1, p2 = _config({'ds4.base_url': 'http://192.0.2.7:8000/v1'})
        with p1, p2, patch('assistant.llm.ChatGPTLLM.OpenAI') as openai_cls:
            Ds4LLM().initialize()
        kwargs = openai_cls.call_args.kwargs
        assert kwargs['base_url'] == 'http://192.0.2.7:8000/v1'
        assert kwargs['api_key']


class TestModelAndEffort:
    def test_tier_model_wins_over_the_ds4_section_which_wins_over_the_default(self):
        p1, p2 = _config({'ds4.model': 'glm-5.3'})
        with p1, p2:
            assert Ds4LLM().model == 'glm-5.3'
            assert Ds4LLM(model='qwen3.8-flash-next').model == 'qwen3.8-flash-next'
        p1, p2 = _config({})
        with p1, p2:
            assert Ds4LLM().model == Ds4LLM.DEFAULT_MODEL

    def test_an_unknown_effort_spelling_falls_back_to_the_default(self):
        p1, p2 = _config({'ds4.reasoning_effort': 'lots'})
        with p1, p2:
            assert Ds4LLM.reasoning_effort() == Ds4LLM.DEFAULT_REASONING_EFFORT

    def test_a_request_without_thinking_sends_the_configured_effort_and_a_thinking_one_high(self):
        # Contract with ds4-server: the effort travels as the `reasoning_effort` body field and
        # "none" is its spelling for no reasoning at all.
        llm, create = _llm_with_stream([_chunk(content="x")], {'ds4.reasoning_effort': 'none'})
        p1, p2 = _config({'ds4.reasoning_effort': 'none'})
        with p1, p2:
            list(llm.create_streaming_completion([{'role': 'user', 'content': 'hi'}]))
            assert create.call_args.kwargs['extra_body'] == {'reasoning_effort': 'none'}
            assert not llm.handles_thinking_internally()
            create.return_value = iter([_chunk(content="y")])
            list(llm.create_streaming_completion([{'role': 'user', 'content': 'hi'}], thinking=True))
            assert create.call_args.kwargs['extra_body'] == {'reasoning_effort': Ds4LLM.THINKING_REASONING_EFFORT}

    def test_the_model_counts_as_thinking_internally_unless_reasoning_is_off(self):
        p1, p2 = _config({'ds4.reasoning_effort': 'medium'})
        with p1, p2:
            assert Ds4LLM().handles_thinking_internally()


class TestStreaming:
    def test_reasoning_deltas_are_wrapped_in_one_thinking_block_before_the_reply(self):
        usage = SimpleNamespace(prompt_tokens=61, completion_tokens=92,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=40))
        llm, _ = _llm_with_stream([
            _chunk(reasoning="Let me "), _chunk(reasoning="see."), _chunk(content="Turn "),
            _chunk(content="it on."), _chunk(choices=False, usage=usage),
        ])
        p1, p2 = _config({})
        with p1, p2:
            out = list(llm.create_streaming_completion([{'role': 'user', 'content': 'hi'}]))
        assert out == ["<thinking>", "Let me ", "see.", "</thinking>", "Turn ", "it on."]

    def test_a_stream_that_ends_while_reasoning_still_closes_the_block(self):
        llm, _ = _llm_with_stream([_chunk(reasoning="hmm"), _chunk(choices=False)])
        p1, p2 = _config({})
        with p1, p2:
            out = list(llm.create_streaming_completion([{'role': 'user', 'content': 'hi'}]))
        assert out[-1] == "</thinking>" and "".join(out).count("<thinking>") == 1

    def test_a_reply_without_reasoning_carries_no_thinking_tags(self):
        llm, _ = _llm_with_stream([_chunk(content="Hej!"), _chunk(choices=False)])
        p1, p2 = _config({})
        with p1, p2:
            assert list(llm.create_streaming_completion([{'role': 'user', 'content': 'hi'}])) == ["Hej!"]

    def test_non_streaming_returns_the_text_and_keeps_the_reasoning_out_of_it(self):
        llm, create = _llm_with_stream([])
        message = SimpleNamespace(content="Hej!", reasoning_content="Swedish hello, one line.")
        create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        p1, p2 = _config({})
        with p1, p2:
            assert llm.create_completion([{'role': 'user', 'content': 'hi'}]) == "Hej!"


class TestLocalOnlyRouting:
    def _get(self, tier_cfg, local_only):
        def get(key, default=None):
            if key == 'ollama.local_only':
                return local_only
            if key.startswith('ai.'):
                return tier_cfg
            return default
        return patch('assistant.llm.config.get', side_effect=get)

    def test_true_still_means_ollama(self):
        with self._get(None, True):
            assert local_only_class() == 'OllamaLLM'
            assert parse_llm_config('smart') == ('OllamaLLM', None)

    def test_a_class_name_routes_every_cloud_tier_there_without_its_cloud_model(self):
        with self._get(None, 'Ds4LLM'):
            for tier in ('fast', 'smart', 'vision', 'video', 'photo_indexing', 'memory_curator', 'cache_auditor'):
                assert parse_llm_config(tier) == ('Ds4LLM', None)

    def test_a_tier_already_on_the_named_class_keeps_its_model(self):
        with self._get({'llm': 'Ds4LLM', 'model': 'glm-5.3'}, 'Ds4LLM'):
            assert parse_llm_config('smart') == ('Ds4LLM', 'glm-5.3')

    def test_a_tier_on_ollama_moves_too_and_loses_its_ollama_model(self):
        with self._get({'llm': 'OllamaLLM', 'model': 'qwen3.8:4b'}, 'Ds4LLM'):
            assert parse_llm_config('cache_auditor') == ('Ds4LLM', None)

    def test_code_quality_stays_on_the_claude_cli(self):
        with self._get({'model': 'claude-sonnet-5-5'}, 'Ds4LLM'):
            assert parse_llm_config('code_quality') == ('ClaudeLLM', 'claude-sonnet-5-5')

    @pytest.mark.parametrize("off", [False, None, "", "   "])
    def test_off_values_leave_the_cloud_tiers_alone(self, off):
        with self._get({'llm': 'GeminiLLM', 'model': 'gemini-3.8-flash'}, off):
            assert local_only_class() is None
            assert parse_llm_config('video') == ('GeminiLLM', 'gemini-3.8-flash')
