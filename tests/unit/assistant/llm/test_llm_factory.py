import sys
import types

import pytest
from unittest.mock import patch

from assistant.llm import create_llm, parse_llm_config
from assistant.llm.VaultLLM import VaultLLM
from common.vault.Vault import Vault


# ===========================================================================
# parse_llm_config — picks the LLM class/model for a tier from config,
# falling back to the cloud defaults that production relies on.
#
# The branching here guards a real near-miss bug (see commit "default to cloud
# LLMs when no `ai` config block is present"): without it, merging the local-AI
# branch would silently switch prod off Gemini+Claude and onto local Ollama.
# ===========================================================================

class TestParseLLMConfig:
    def _patch_config(self, tier_value, local_only=False):
        """Patch assistant.llm.config.get: every ai.<tier> lookup yields tier_value (or raises it),
        ollama.local_only yields local_only, anything else its default."""
        def get(key, default=None):
            if key == 'ollama.local_only':
                return local_only
            if key.startswith('ai.'):
                if isinstance(tier_value, Exception):
                    raise tier_value
                return tier_value
            return default
        p = patch('assistant.llm.config.get', side_effect=get)
        return p, p.start()

    def test_string_value_yields_class_with_no_model(self):
        p, _ = self._patch_config('OllamaLLM')
        try:
            assert parse_llm_config('fast') == ('OllamaLLM', None)
        finally:
            p.stop()

    def test_dict_does_not_inherit_default_model_for_different_class(self):
        # The crucial guard: an explicit Ollama backend must NOT pick up Claude's
        # default model.
        p, _ = self._patch_config({'llm': 'OllamaLLM'})
        try:
            assert parse_llm_config('smart') == ('OllamaLLM', None)
        finally:
            p.stop()

    def test_dict_explicit_model_is_used(self):
        p, _ = self._patch_config({'llm': 'OllamaLLM', 'model': 'gemma4:26b'})
        try:
            assert parse_llm_config('smart') == ('OllamaLLM', 'gemma4:26b')
        finally:
            p.stop()

    def test_unknown_tier_falls_back_to_ollama(self):
        p, _ = self._patch_config(KeyError("ai.bogus"))
        try:
            assert parse_llm_config('bogus') == ('OllamaLLM', None)
        finally:
            p.stop()

    def test_local_only_routes_cloud_default_tier_to_ollama_without_its_model(self):
        # No ai block at all: the cloud default (a Claude/Gemini model name) must not leak to Ollama.
        p, _ = self._patch_config(None, local_only=True)
        try:
            for tier in ('fast', 'smart', 'vision', 'video', 'photo_indexing', 'memory_curator'):
                assert parse_llm_config(tier) == ('OllamaLLM', None)
        finally:
            p.stop()

    def test_local_only_overrides_an_explicit_cloud_tier(self):
        p, _ = self._patch_config({'llm': 'GeminiLLM', 'model': 'gemini-3.8-flash'}, local_only=True)
        try:
            assert parse_llm_config('video') == ('OllamaLLM', None)
        finally:
            p.stop()

    def test_local_only_keeps_the_model_of_a_tier_already_on_ollama(self):
        p, _ = self._patch_config({'llm': 'OllamaLLM', 'model': 'qwen3.8:4b'}, local_only=True)
        try:
            assert parse_llm_config('cache_auditor') == ('OllamaLLM', 'qwen3.8:4b')
        finally:
            p.stop()

    def test_local_only_leaves_code_quality_alone(self):
        # The nightly Claude Code CLI runs cannot move to Ollama; only its model is read.
        p, _ = self._patch_config({'model': 'claude-sonnet-5-5'}, local_only=True)
        try:
            assert parse_llm_config('code_quality') == ('ClaudeLLM', 'claude-sonnet-5-5')
        finally:
            p.stop()

    def test_local_only_off_leaves_cloud_tiers_untouched(self):
        p, _ = self._patch_config({'llm': 'GeminiLLM', 'model': 'gemini-3.8-flash'}, local_only=False)
        try:
            assert parse_llm_config('video') == ('GeminiLLM', 'gemini-3.8-flash')
        finally:
            p.stop()

# ===========================================================================
# create_llm — instantiates an LLM by class name, forwarding model and (only
# when accepted) prompt_cache, and wraps the result in a VaultLLM. We inject a
# fake module so the test exercises the factory's kwargs/introspection logic
# without constructing real backends (which need API keys).
# ===========================================================================

class _FakeLLMWithCache:
    def __init__(self, model='default-model', prompt_cache=True):
        self.model = model
        self.prompt_cache = prompt_cache


class _FakeLLMNoCache:
    def __init__(self, model='default-model'):
        self.model = model


class TestCreateLLM:
    @pytest.fixture(autouse=True)
    def fresh_vault_singleton(self):
        # VaultLLM reads Vault.get_instance(); never let one test's singleton leak into another.
        Vault.reset_instance()
        yield
        Vault.reset_instance()

    def _inject(self, class_name, cls):
        """Register a fake assistant.llm.<class_name> module exposing cls."""
        module = types.ModuleType(f"assistant.llm.{class_name}")
        setattr(module, class_name, cls)
        sys.modules[f"assistant.llm.{class_name}"] = module
        return module

    def test_model_override_forwarded(self):
        self._inject("_FakeLLMNoCache", _FakeLLMNoCache)
        try:
            inst = create_llm("_FakeLLMNoCache", model="my-model")
            assert inst.model == "my-model"
        finally:
            del sys.modules["assistant.llm._FakeLLMNoCache"]

    def test_no_model_uses_constructor_default(self):
        self._inject("_FakeLLMNoCache", _FakeLLMNoCache)
        try:
            inst = create_llm("_FakeLLMNoCache")
            assert inst.model == "default-model"
        finally:
            del sys.modules["assistant.llm._FakeLLMNoCache"]

    def test_prompt_cache_forwarded_when_accepted(self):
        self._inject("_FakeLLMWithCache", _FakeLLMWithCache)
        try:
            inst = create_llm("_FakeLLMWithCache", prompt_cache=False)
            assert inst.prompt_cache is False
        finally:
            del sys.modules["assistant.llm._FakeLLMWithCache"]

    def test_prompt_cache_ignored_when_not_accepted(self):
        # Must not raise even though the constructor has no prompt_cache param.
        self._inject("_FakeLLMNoCache", _FakeLLMNoCache)
        try:
            inst = create_llm("_FakeLLMNoCache", prompt_cache=True)
            assert isinstance(inst.llm, _FakeLLMNoCache)
        finally:
            del sys.modules["assistant.llm._FakeLLMNoCache"]

    def test_result_is_a_vault_llm_around_the_backend(self):
        # Every LLM the system builds goes through the vault boundary (VAULT_PLAN §3.6);
        # the backend itself is reachable as .llm, and its attributes pass through.
        self._inject("_FakeLLMWithCache", _FakeLLMWithCache)
        try:
            inst = create_llm("_FakeLLMWithCache", model="my-model", prompt_cache=True)
            assert isinstance(inst, VaultLLM)
            assert isinstance(inst.llm, _FakeLLMWithCache)
            assert inst.model == "my-model" and inst.prompt_cache is True
        finally:
            del sys.modules["assistant.llm._FakeLLMWithCache"]
