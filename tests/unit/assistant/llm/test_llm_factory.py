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
    def _patch_config(self, side_effect):
        """Patch assistant.llm.config.get with the given return value or side_effect."""
        p = patch('assistant.llm.config.get')
        mock = p.start()
        if isinstance(side_effect, Exception) or (isinstance(side_effect, type) and issubclass(side_effect, Exception)):
            mock.side_effect = side_effect
        else:
            mock.return_value = side_effect
        return p, mock

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

    def test_fast_tier_default_is_gemini_35_flash(self):
        # The fast tier is the voice loop. Its default must pin the exact Flash
        # model: drifting to another one silently changes voice latency, the
        # thinking floor (gemini-3.7+ → LOW) and the kv-cache behavior.
        p, _ = self._patch_config(KeyError("ai.fast"))
        try:
            assert parse_llm_config('fast') == ('GeminiLLM', 'gemini-3.5-flash')
        finally:
            p.stop()

    def test_smart_tier_default_is_opus_5_5(self):
        # The smart tier is the chat loop (Telegram, TUI). Opus 5.5 thinks adaptively on
        # its own, so drifting to an older model would change the thinking-tag handling.
        p, _ = self._patch_config(KeyError("ai.smart"))
        try:
            assert parse_llm_config('smart') == ('ClaudeLLM', 'claude-opus-5-5')
        finally:
            p.stop()

    def test_cache_auditor_default_is_gemini(self):
        # The cache auditor is intentionally off-Claude (cheap second opinion);
        # accidentally flipping it onto ClaudeLLM would burn through the audit budget.
        p, _ = self._patch_config(KeyError("ai.cache_auditor"))
        try:
            assert parse_llm_config('cache_auditor')[0] == 'GeminiLLM'
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

    def test_unknown_class_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown LLM class"):
            create_llm("NoSuchLLMClass")

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
