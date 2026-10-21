"""`ollama.think` — the config knob over OllamaLLM's native thinking channel."""
from unittest.mock import patch

import pytest

from assistant.llm.OllamaLLM import OllamaLLM


def _config(values: dict):
    def get(key, default=None):
        return values.get(key, default)
    return patch('assistant.llm.OllamaLLM.config.get', side_effect=get)


@pytest.fixture
def thinking_model():
    """An OllamaLLM whose model /api/show lists as thinking and tool-taking, no probe."""
    llm = OllamaLLM('qwen-test', base_url='http://ollama.example:11434')
    with patch.dict(OllamaLLM._capabilities, {(llm.model, llm.base_url): {'thinking', 'tools', 'completion'}}):
        yield llm


class TestThinkSetting:
    def test_false_turns_the_channel_off_and_hands_the_prompt_hack_the_turn(self, thinking_model):
        # Contract with Ollama's /api/chat: `think` false is how the channel is declined.
        with _config({'ollama.think': False}):
            assert thinking_model._build_request([], stream=True)['think'] is False
            assert not thinking_model.handles_thinking_internally()

    @pytest.mark.parametrize('level', ['low', 'Medium', 'HIGH'])
    def test_a_level_travels_as_the_think_value_and_counts_as_thinking(self, thinking_model, level):
        with _config({'ollama.think': level}):
            assert thinking_model._build_request([], stream=True)['think'] == level.lower()
            assert thinking_model.handles_thinking_internally()

    def test_absent_key_sends_medium_the_level_that_scored_best(self, thinking_model):
        """Measured 2026-10-21 on the prompt test suite: medium 94.9–95.5% against 92.1–92.7% at true."""
        with _config({}):
            assert thinking_model._build_request([], stream=True)['think'] == 'medium'

    def test_true_spellings_leave_the_level_to_the_model(self, thinking_model):
        for value in ({'ollama.think': True}, {'ollama.think': 'yes'}):
            with _config(value):
                assert thinking_model._build_request([], stream=True)['think'] is True

    def test_an_unknown_spelling_falls_back_to_the_default_level(self, thinking_model):
        with _config({'ollama.think': 'lots'}):
            assert thinking_model._build_request([], stream=True)['think'] == 'medium'

    def test_a_level_never_reaches_a_model_without_the_capability(self):
        llm = OllamaLLM('gemma-test', base_url='http://ollama.example:11434')
        with patch.dict(OllamaLLM._capabilities, {(llm.model, llm.base_url): {'completion'}}), \
                _config({'ollama.think': 'high'}):
            assert llm._build_request([], stream=True)['think'] is False
            assert not llm.handles_thinking_internally()
