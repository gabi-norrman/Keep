import base64
import itertools
import pytest
from unittest.mock import Mock, patch, MagicMock

from assistant.llm.AbstractLLM import AbstractLLM, LLMRefusalError, ThinkingNotSupportedError


# ---------------------------------------------------------------------------
# Concrete subclass of AbstractLLM for testing base-class behaviour
# ---------------------------------------------------------------------------

class ConcreteTestLLM(AbstractLLM):
    def __init__(self):
        self.client = None
        self._completion_response = "response"
        self._streaming_chunks = ["chunk1", "chunk2"]
        self._image_response = "image_response"

    def name(self) -> str:
        return "TestLLM"

    def initialize(self):
        self.client = "initialized"

    def create_streaming_completion(self, messages, thinking=False):
        self._ensure_initialized()
        for c in self._streaming_chunks:
            yield c

    def create_completion(self, messages, thinking=False):
        self._ensure_initialized()
        return self._completion_response

    def create_completion_with_images(self, messages, images):
        self._ensure_initialized()
        return self._image_response


# ===========================================================================
# TestAbstractLLMRetry
# ===========================================================================

class TestAbstractLLMRetry:
    def test_think_delegates_to_create_completion(self):
        llm = ConcreteTestLLM()
        llm.initialize()
        result = llm.think("sys", "user")
        assert result == "response"

    def test_think_with_images_delegates_to_create_completion_with_images(self):
        llm = ConcreteTestLLM()
        llm.initialize()
        result = llm.think("sys", "user", images=["img1"])
        assert result == "image_response"

    def test_video_is_unsupported_by_default(self):
        llm = ConcreteTestLLM()
        assert llm.supports_video() is False
        assert llm.max_video_bytes() == 0
        with pytest.raises(NotImplementedError, match="TestLLM cannot take video input"):
            llm.think_with_video("sys", "user", b"vid", "video/mp4")

    def test_think_with_messages_delegates_to_create_completion(self):
        llm = ConcreteTestLLM()
        llm.initialize()
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        result = llm.think_with_messages(msgs)
        assert result == "response"

    def test_ensure_initialized_raises_without_init(self):
        llm = ConcreteTestLLM()
        with pytest.raises(RuntimeError, match="not initialized"):
            llm.create_completion([])

    def test_supports_native_thinking_default_false(self):
        llm = ConcreteTestLLM()
        assert llm.supports_native_thinking() is False

    def test_handles_thinking_internally_default_false(self):
        assert ConcreteTestLLM().handles_thinking_internally() is False

    def test_prefers_prepended_user_query_default_true(self):
        llm = ConcreteTestLLM()
        assert llm.prefers_prepended_user_query() is True

    @patch('assistant.llm.AbstractLLM.time.sleep')
    def test_retry_retries_on_failure(self, mock_sleep):
        llm = ConcreteTestLLM()
        llm.initialize()
        call_count = [0]
        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("fail")
            return "ok"
        result = llm._retry(flaky, max_retries=3)
        assert result == "ok"
        assert call_count[0] == 3

    @patch('assistant.llm.AbstractLLM.time.sleep')
    def test_retry_raises_after_max_retries(self, mock_sleep):
        llm = ConcreteTestLLM()
        llm.initialize()
        def always_fail():
            raise RuntimeError("permanent")
        with pytest.raises(RuntimeError, match="permanent"):
            llm._retry(always_fail, max_retries=2)

    @patch('assistant.llm.AbstractLLM.time.sleep')
    def test_retry_does_not_repeat_a_refusal(self, mock_sleep):
        """A refusal is deterministic: retrying it only burns time and money, and the caller
        (the autonomous check) has a fallback model for it."""
        llm = ConcreteTestLLM()
        llm.initialize()
        calls = [0]
        def refused():
            calls[0] += 1
            raise LLMRefusalError("refused")
        with pytest.raises(LLMRefusalError):
            llm._retry(refused, max_retries=3)
        assert calls[0] == 1
        mock_sleep.assert_not_called()


# ===========================================================================
# TestChatGPTLLM
# ===========================================================================

class TestChatGPTLLM:
    @patch('assistant.llm.ChatGPTLLM.config')
    def test_constructor_params(self, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM(model='gpt-4', api_key_name='openai', base_url='http://test')
        assert llm.model == 'gpt-4'
        assert llm.api_key == "test-key"
        assert llm.base_url == 'http://test'
        assert llm.client is None
        mock_config.get_api_key.assert_called_with('openai')

    @patch('assistant.llm.ChatGPTLLM.config')
    @patch('assistant.llm.ChatGPTLLM.OpenAI')
    def test_initialize_creates_client(self, MockOpenAI, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.initialize()
        MockOpenAI.assert_called_once_with(api_key="test-key")
        assert llm.client is not None

    @patch('assistant.llm.ChatGPTLLM.config')
    @patch('assistant.llm.ChatGPTLLM.OpenAI')
    def test_initialize_with_base_url(self, MockOpenAI, mock_config):
        mock_config.get_api_key.return_value = "test-key"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM(base_url="http://custom")
        llm.initialize()
        MockOpenAI.assert_called_once_with(api_key="test-key", base_url="http://custom")

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_name_returns_gpt(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        assert ChatGPTLLM().name() == "GPT"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_supports_native_thinking_returns_false(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        assert ChatGPTLLM().supports_native_thinking() is False

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_streaming_raises_on_thinking(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()
        with pytest.raises(ThinkingNotSupportedError):
            list(llm.create_streaming_completion([], thinking=True))

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_completion_raises_on_thinking(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()
        with pytest.raises(ThinkingNotSupportedError):
            llm.create_completion([], thinking=True)

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_streaming_yields_chunks(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()

        chunk1 = Mock()
        chunk1.choices = [Mock(delta=Mock(content="Hello"))]
        chunk2 = Mock()
        chunk2.choices = [Mock(delta=Mock(content=None))]
        chunk3 = Mock()
        chunk3.choices = [Mock(delta=Mock(content=" world"))]

        llm.client = Mock()
        llm.client.chat.completions.create.return_value = [chunk1, chunk2, chunk3]

        result = list(llm.create_streaming_completion([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " world"]

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_completion_returns_string(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()

        response = Mock()
        response.choices = [Mock(message=Mock(content="result text"))]
        llm.client.chat.completions.create.return_value = response

        result = llm.create_completion([{"role": "user", "content": "hi"}])
        assert result == "result text"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_process_image_bytes_png(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        png_bytes = b'\x89PNG' + b'\x00' * 10
        result = ChatGPTLLM._process_image(png_bytes)
        assert result.startswith("data:image/png;base64,")

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_process_image_bytes_jpeg(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        jpeg_bytes = b'\xff\xd8\xff' + b'\x00' * 10
        result = ChatGPTLLM._process_image(jpeg_bytes)
        assert result.startswith("data:image/jpeg;base64,")

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_process_image_base64_string(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        b64 = base64.b64encode(b"test image data").decode()
        result = ChatGPTLLM._process_image(b64)
        assert result.startswith("data:image/jpeg;base64,")
        assert b64 in result

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_process_image_unsupported_type(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        with pytest.raises(ValueError, match="Unsupported image format"):
            ChatGPTLLM._process_image(12345)

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_create_completion_with_images(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()

        response = Mock()
        response.choices = [Mock(message=Mock(content="saw image"))]
        llm.client.chat.completions.create.return_value = response

        png_bytes = b'\x89PNG' + b'\x00' * 10
        msgs = [{"role": "user", "content": "what is this?"}]
        result = llm.create_completion_with_images(msgs, [png_bytes])
        assert result == "saw image"

        call_args = llm.client.chat.completions.create.call_args
        processed = call_args[1]['messages']
        assert processed[0]['role'] == 'user'
        assert isinstance(processed[0]['content'], list)
        assert processed[0]['content'][0]['type'] == 'text'
        assert processed[0]['content'][1]['type'] == 'image_url'

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_completion_strips_cache_markers_from_system(self, mock_config):
        # Regression for the marker leak: OpenAI-compatible backends (and every
        # ChatGPTLLM subclass — Grok, OpenRouter, …) received Claude's cache
        # breakpoint markers as literal system-prompt text.
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()

        response = Mock()
        response.choices = [Mock(message=Mock(content="ok"))]
        llm.client.chat.completions.create.return_value = response

        llm.create_completion([
            {"role": "system", "content": "rules<<<CACHE_BREAKPOINT>>>live data"},
            {"role": "user", "content": "hi"},
        ])
        sent = llm.client.chat.completions.create.call_args[1]['messages']
        assert sent[0] == {"role": "system", "content": "ruleslive data"}
        assert sent[1] == {"role": "user", "content": "hi"}

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_completion_with_images_strips_cache_markers_from_system(self, mock_config):
        # The image path builds its messages separately from create_completion, so it
        # needs the same strip — a camera query on a GPT tier must not leak markers.
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()

        response = Mock()
        response.choices = [Mock(message=Mock(content="saw image"))]
        llm.client.chat.completions.create.return_value = response

        png_bytes = b'\x89PNG' + b'\x00' * 10
        llm.create_completion_with_images([
            {"role": "system", "content": "rules<<<CACHE_BREAKPOINT>>>live data"},
            {"role": "user", "content": "what is this?"},
        ], [png_bytes])
        sent = llm.client.chat.completions.create.call_args[1]['messages']
        assert sent[0] == {"role": "system", "content": "ruleslive data"}
        assert sent[1]['content'][1]['type'] == 'image_url'

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_ensure_initialized_raises(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        with pytest.raises(RuntimeError, match="not initialized"):
            llm.create_completion([])


# ===========================================================================
# TestClaudeLLM
# ===========================================================================

class TestClaudeLLM:
    @patch('assistant.llm.ClaudeLLM.config')
    def test_constructor(self, mock_config):
        mock_config.get_api_key.return_value = "claude-key"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(model='claude-sonnet-4-5')
        assert llm.model == 'claude-sonnet-4-5'
        assert llm.api_key == "claude-key"
        assert llm.client is None

    @patch('assistant.llm.ClaudeLLM.config')
    @patch('assistant.llm.ClaudeLLM.Anthropic')
    def test_initialize_creates_client(self, MockAnthropic, mock_config):
        mock_config.get_api_key.return_value = "claude-key"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.initialize()
        MockAnthropic.assert_called_once_with(api_key="claude-key")

    @patch('assistant.llm.ClaudeLLM.config')
    def test_name_returns_claude(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM().name() == "Claude (claude-sonnet-4-5)"

    @patch('assistant.llm.ClaudeLLM.config')
    def test_supports_native_thinking_returns_true(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM().supports_native_thinking() is True

    @pytest.mark.parametrize("model, internal", [
        ("claude-fable-5-1", True), ("claude-mythos-5-1", True), ("claude-opus-5", True), ("claude-sonnet-5", True),
        ("claude-opus-4-8", False), ("claude-sonnet-4-5", False), ("claude-haiku-4-5", False),
    ])
    @patch('assistant.llm.ClaudeLLM.config')
    def test_handles_thinking_internally(self, mock_config, model, internal):
        """The 5-generation families think adaptively on their own; a <thinking>-tag instruction
        gets the request refused as reasoning extraction, so nothing may send them one. An
        older model only thinks when asked and must keep the instruction."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM(model).handles_thinking_internally() is internal

    @patch('assistant.llm.ClaudeLLM.config')
    def test_the_default_model_keeps_the_thinking_instruction(self, mock_config):
        """A bare `llm: ClaudeLLM` override inherits the constructor default, which does not
        think on its own."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM().handles_thinking_internally() is False

    @patch('assistant.llm.ClaudeLLM.config')
    def test_prefers_prepended_user_query_true_for_non_fable(self, mock_config):
        """Non-Fable Claude prepends the user query: it helps intelligence enough to be
        worth defeating prompt caching (the caller disables caching when prepending)."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM(model="claude-sonnet-4-5").prefers_prepended_user_query() is True

    @patch('assistant.llm.ClaudeLLM.config')
    def test_prefers_prepended_user_query_false_for_fable(self, mock_config):
        """Fable opts out of prepending so the shared system-prompt prefix stays
        cacheable (prepending puts per-request bytes at position 0 of the prefix)."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM(model="claude-fable-5").prefers_prepended_user_query() is False

    @patch('assistant.llm.ClaudeLLM.config')
    def test_set_prompt_cache_toggles_flag(self, mock_config):
        """Prepending defeats caching, so the caller disables it via set_prompt_cache."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.set_prompt_cache(False)
        assert llm.prompt_cache is False
        llm.set_prompt_cache(True)
        assert llm.prompt_cache is True

    @patch('assistant.llm.ClaudeLLM.config')
    def test_convert_messages_extracts_system(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "sys msg"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "should be filtered"},
        ]
        system, converted = llm._convert_messages(messages)
        assert system == "sys msg"
        assert len(converted) == 2
        assert converted[0]['role'] == 'user'
        assert converted[1]['role'] == 'assistant'

    @patch('assistant.llm.ClaudeLLM.config')
    def test_build_api_params_without_thinking(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        assert params['max_tokens'] == 4096
        assert 'thinking' not in params
        assert params['stream'] is True

    @patch('assistant.llm.ClaudeLLM.config')
    def test_build_api_params_with_thinking(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        params = llm._build_api_params(messages, thinking=True, stream=False)
        assert params['max_tokens'] == 32000
        assert params['thinking'] == {"type": "adaptive"}
        assert params['stream'] is False

    @patch('assistant.llm.ClaudeLLM.config')
    def test_non_streaming_request_carries_an_explicit_timeout(self, mock_config):
        """Above ~21k max_tokens the SDK refuses a non-streaming call unless a timeout is
        given; a streaming call needs none."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [{"role": "user", "content": "hi"}]
        assert llm._build_api_params(messages, thinking=True, stream=False)['timeout'] == llm.NON_STREAMING_TIMEOUT
        assert 'timeout' not in llm._build_api_params(messages, thinking=True, stream=True)

    @patch('assistant.llm.ClaudeLLM.config')
    def test_thinking_cap_passes_the_sdk_non_streaming_guard(self, mock_config):
        """The real SDK check: the cap with the explicit timeout must not raise the
        'Streaming is required' ValueError, and would without it."""
        mock_config.get_api_key.return_value = "k"
        from anthropic import Anthropic
        from assistant.llm.ClaudeLLM import ClaudeLLM
        client = Anthropic(api_key="k")
        with pytest.raises(ValueError, match="Streaming is required"):
            client._calculate_nonstreaming_timeout(ClaudeLLM.THINKING_MAX_TOKENS, None)
        llm = ClaudeLLM()
        params = llm._build_api_params([{"role": "user", "content": "hi"}], thinking=True, stream=False)
        assert params['timeout'].read >= 3600 * params['max_tokens'] / 128_000

    @patch('assistant.llm.ClaudeLLM.config')
    def test_completion_records_the_stop_reason(self, mock_config):
        """A caller that persists a reply verbatim (the memory curator) must be able to
        tell a reply cut off at max_tokens from a finished one."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        assert llm.last_stop_reason is None
        llm.client = Mock()
        response = Mock()
        response.content = [Mock(type='text', text='half a memory ba')]
        response.usage = Mock(cache_creation_input_tokens=0, cache_read_input_tokens=0, input_tokens=0,
                              output_tokens=32000)
        response.stop_reason = 'max_tokens'
        llm.client.messages.create.return_value = response

        with patch('assistant.llm.ClaudeLLM.info') as mock_info:
            text = llm.create_completion([{"role": "user", "content": "curate"}], thinking=True)

        assert text == 'half a memory ba'
        assert llm.last_stop_reason == 'max_tokens'
        assert any("max_tokens" in str(c.args[0]) for c in mock_info.call_args_list)

        response.stop_reason = 'end_turn'
        llm.create_completion([{"role": "user", "content": "curate"}], thinking=True)
        assert llm.last_stop_reason == 'end_turn'

    @patch('assistant.llm.ClaudeLLM.config')
    def test_name_returns_claude_fast(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        assert ClaudeLLM(speed="fast").name() == "Claude (claude-sonnet-4-5) (fast)"

    @patch('assistant.llm.ClaudeLLM.config')
    def test_create_message_uses_beta_when_fast(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(speed="fast")
        llm.client = Mock()

        api_params = {"model": "test", "messages": [], "max_tokens": 100, "stream": False}
        llm._create_message(api_params)

        llm.client.beta.messages.create.assert_called_once()
        call_kwargs = llm.client.beta.messages.create.call_args
        assert call_kwargs[1]['speed'] == "fast"
        assert ClaudeLLM.FAST_MODE_BETA in call_kwargs[1]['betas']

    @patch('assistant.llm.ClaudeLLM.config')
    def test_create_message_uses_standard_when_not_fast(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.client = Mock()

        api_params = {"model": "test", "messages": [], "max_tokens": 100, "stream": False}
        llm._create_message(api_params)

        llm.client.messages.create.assert_called_once()
        llm.client.beta.messages.create.assert_not_called()

    @patch('assistant.llm.ClaudeLLM.time.sleep')
    @patch('assistant.llm.ClaudeLLM.config')
    def test_streaming_529_retry_succeeds(self, mock_config, mock_sleep):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        llm = ClaudeLLM()
        llm.client = Mock()

        error_response = httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com"))
        api_error = APIStatusError("overloaded", response=error_response, body=None)

        event = Mock(type='content_block_delta', delta=Mock(type='text_delta', text='recovered'))
        llm.client.messages.create.side_effect = [api_error, [event]]

        result = list(llm.create_streaming_completion([{"role": "user", "content": "hi"}]))
        assert result == ['recovered']
        mock_sleep.assert_called_once_with(0.5)
        assert llm.client.messages.create.call_count == 2

    @patch('assistant.llm.ClaudeLLM.config')
    def test_streaming_non_529_error_raises(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        llm = ClaudeLLM()
        llm.client = Mock()

        error_response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com"))
        api_error = APIStatusError("bad request", response=error_response, body=None)
        llm.client.messages.create.side_effect = api_error

        with pytest.raises(APIStatusError) as exc_info:
            list(llm.create_streaming_completion([{"role": "user", "content": "hi"}]))
        assert exc_info.value.status_code == 400

    @patch('assistant.llm.ClaudeLLM.time.sleep')
    @patch('assistant.llm.ClaudeLLM.config')
    def test_completion_529_retry_succeeds(self, mock_config, mock_sleep):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        llm = ClaudeLLM()
        llm.client = Mock()

        error_response = httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com"))
        api_error = APIStatusError("overloaded", response=error_response, body=None)

        block = Mock(type='text', text='recovered')
        response = Mock()
        response.content = [block]
        response.usage = Mock(input_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0)

        llm.client.messages.create.side_effect = [api_error, response]

        result = llm.create_completion([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        mock_sleep.assert_called_once_with(0.5)

    @patch('assistant.llm.ClaudeLLM.config')
    def test_completion_non_529_error_raises(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        llm = ClaudeLLM()
        llm.client = Mock()

        error_response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com"))
        api_error = APIStatusError("bad request", response=error_response, body=None)
        llm.client.messages.create.side_effect = api_error

        with pytest.raises(APIStatusError) as exc_info:
            llm.create_completion([{"role": "user", "content": "hi"}])
        assert exc_info.value.status_code == 400

    @patch('assistant.llm.ClaudeLLM.config')
    def test_is_retryable_status_529(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        error_response = httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com"))
        e = APIStatusError("overloaded", response=error_response, body=None)
        assert ClaudeLLM._is_retryable(e) is True

    @patch('assistant.llm.ClaudeLLM.config')
    def test_is_retryable_body_type(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        error_response = httpx.Response(500, request=httpx.Request("POST", "https://api.anthropic.com"))
        body = {"error": {"type": "overloaded_error", "message": "Overloaded"}}
        e = APIStatusError("overloaded", response=error_response, body=body)
        assert ClaudeLLM._is_retryable(e) is True

    @patch('assistant.llm.ClaudeLLM.config')
    def test_is_retryable_non_overloaded_error(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        error_response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com"))
        e = APIStatusError("bad request", response=error_response, body=None)
        assert ClaudeLLM._is_retryable(e) is False

    @patch('assistant.llm.ClaudeLLM.time.sleep')
    @patch('assistant.llm.ClaudeLLM.config')
    def test_completion_retries_on_body_overloaded_error(self, mock_config, mock_sleep):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        import httpx
        from anthropic import APIStatusError

        llm = ClaudeLLM()
        llm.client = Mock()

        error_response = httpx.Response(500, request=httpx.Request("POST", "https://api.anthropic.com"))
        body = {"error": {"type": "overloaded_error", "message": "Overloaded"}}
        api_error = APIStatusError("overloaded", response=error_response, body=body)

        block = Mock(type='text', text='recovered')
        response = Mock()
        response.content = [block]
        response.usage = Mock(input_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0)

        llm.client.messages.create.side_effect = [api_error, response]

        result = llm.create_completion([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        mock_sleep.assert_called_once_with(0.5)

    @patch('assistant.llm.ClaudeLLM.config')
    def test_streaming_yields_text_deltas(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.client = Mock()

        event1 = Mock(type='content_block_delta', delta=Mock(type='text_delta', text='Hello'))
        event2 = Mock(type='content_block_start', delta=Mock(type='start'))
        event3 = Mock(type='content_block_delta', delta=Mock(type='text_delta', text=' world'))

        llm.client.messages.create.return_value = [event1, event2, event3]

        result = list(llm.create_streaming_completion([{"role": "user", "content": "hi"}]))
        assert result == ['Hello', ' world']

    @patch('assistant.llm.ClaudeLLM.config')
    def test_completion_concatenates_text_blocks(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.client = Mock()

        block1 = Mock(type='text', text='Part 1')
        block2 = Mock(type='thinking', text='thought')
        block3 = Mock(type='text', text=' Part 2')

        response = Mock()
        response.content = [block1, block2, block3]
        response.usage = Mock(input_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        llm.client.messages.create.return_value = response

        result = llm.create_completion([{"role": "user", "content": "hi"}])
        assert result == "Part 1 Part 2"

    @patch('assistant.llm.ClaudeLLM.config')
    def test_build_api_params_cache_control_with_list_content(self, mock_config):
        """When previous message content is a list (image blocks), cache_control is added to last block."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc"}},
                {"type": "text", "text": "What is this?"},
            ]},
            {"role": "assistant", "content": "It's a cat."},
            {"role": "user", "content": "Tell me more."},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        prev_msg = params['messages'][-2]
        assert isinstance(prev_msg['content'], list)
        assert prev_msg['content'][-1].get('cache_control') == {"type": "ephemeral"}

    @patch('assistant.llm.ClaudeLLM.config')
    def test_build_api_params_cache_control_with_string_content(self, mock_config):
        """When previous message content is a string, it's wrapped in a list with cache_control."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow up"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        prev_msg = params['messages'][-2]
        assert isinstance(prev_msg['content'], list)
        assert prev_msg['content'][0]['type'] == 'text'
        assert prev_msg['content'][0].get('cache_control') == {"type": "ephemeral"}

    @patch('assistant.llm.ClaudeLLM.config')
    def test_build_api_params_skips_cache_on_empty_string_block(self, mock_config):
        """A blank [-2] turn (e.g. an interrupted reply stored empty) must not get
        cache_control — Anthropic rejects cache_control on empty text blocks."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": "hej"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        prev_msg = params['messages'][-2]
        assert prev_msg['content'] == "   "

    @patch('assistant.llm.ClaudeLLM.config')
    def test_build_api_params_skips_cache_on_empty_text_list_block(self, mock_config):
        """When the [-2] block is a list ending in an empty text block, skip caching."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},
            {"role": "user", "content": "hej"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        prev_msg = params['messages'][-2]
        assert 'cache_control' not in prev_msg['content'][-1]

    @patch('assistant.llm.ClaudeLLM.config')
    def test_cache_breakpoint_marker_splits_system_into_two_blocks(self, mock_config):
        """Marker in system message produces a 1h-cached static prefix and an uncached volatile tail."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(static_cache_ttl="1h")
        sys_msg = "static stable rules\n<<<CACHE_BREAKPOINT>>>\nlive home automation data"
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": "hi"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        system = params['system']
        assert isinstance(system, list)
        assert len(system) == 2
        assert system[0]['text'] == "static stable rules"
        assert system[0]['cache_control'] == {"type": "ephemeral", "ttl": "1h"}
        assert system[1]['text'] == "live home automation data"
        assert 'cache_control' not in system[1]

    @patch('assistant.llm.ClaudeLLM.config')
    def test_static_cache_ttl_override_applies_to_prefix_block(self, mock_config):
        """static_cache_ttl='5m' (the prompt test runner's setting) ttls the cached prefix block."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(static_cache_ttl="5m")
        sys_msg = "static stable rules<<<CACHE_BREAKPOINT>>>live data"
        params = llm._build_api_params(
            [{"role": "system", "content": sys_msg}, {"role": "user", "content": "hi"}],
            thinking=False, stream=True,
        )
        assert params['system'][0]['cache_control'] == {"type": "ephemeral", "ttl": "5m"}

    @patch('assistant.llm.ClaudeLLM.config')
    def test_no_marker_falls_back_to_single_5m_block(self, mock_config):
        """Without the marker, system stays as one ephemeral block (existing behavior)."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "no marker here"},
            {"role": "user", "content": "hi"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        system = params['system']
        assert isinstance(system, list)
        assert len(system) == 1
        assert system[0]['text'] == "no marker here"
        assert system[0]['cache_control'] == {"type": "ephemeral"}

    @patch('assistant.llm.ClaudeLLM.config')
    def test_marker_stripped_when_prompt_cache_disabled(self, mock_config):
        """When prompt_cache=False, the marker must not leak into the system prompt."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(prompt_cache=False)
        sys_msg = "before<<<CACHE_BREAKPOINT>>>after"
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": "hi"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        assert params['system'] == "beforeafter"

    @patch('assistant.llm.ClaudeLLM.config')
    def test_marker_with_empty_tail_omits_second_block(self, mock_config):
        """If the tail after the marker is empty, only the cached static block is sent."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        messages = [
            {"role": "system", "content": "all static<<<CACHE_BREAKPOINT>>>"},
            {"role": "user", "content": "hi"},
        ]
        params = llm._build_api_params(messages, thinking=False, stream=True)
        system = params['system']
        assert len(system) == 1
        assert system[0]['text'] == "all static"
        assert system[0]['cache_control'] == {"type": "ephemeral", "ttl": llm.static_cache_ttl}

    @patch('assistant.llm.ClaudeLLM.config')
    def test_multiple_markers_produce_one_cached_block_each(self, mock_config):
        """Each marker ends a cached block (stable → volatile); text after the
        last marker is the uncached tail."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(static_cache_ttl="1h")
        sys_msg = ("instructions\n<<<CACHE_BREAKPOINT>>>\nplaylists\n"
                   "<<<CACHE_BREAKPOINT>>>\nweather and home data\n"
                   "<<<CACHE_BREAKPOINT>>>\nvolatile tail")
        params = llm._build_api_params(
            [{"role": "system", "content": sys_msg}, {"role": "user", "content": "hi"}],
            thinking=False, stream=True,
        )
        system = params['system']
        assert [b['text'] for b in system] == [
            "instructions", "playlists", "weather and home data", "volatile tail",
        ]
        for cached_block in system[:3]:
            assert cached_block['cache_control'] == {"type": "ephemeral", "ttl": "1h"}
        assert 'cache_control' not in system[3]

    @patch('assistant.llm.ClaudeLLM.config')
    def test_markers_beyond_limit_join_the_uncached_tail(self, mock_config):
        """Anthropic allows 4 breakpoints per request and one is reserved for the
        message-history breakpoint — extra system markers must not produce a 5th
        block; their segments ride uncached instead."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        sys_msg = "<<<CACHE_BREAKPOINT>>>".join(["s1", "s2", "s3", "s4", "s5"])  # 4 markers
        params = llm._build_api_params(
            [{"role": "system", "content": sys_msg}, {"role": "user", "content": "hi"}],
            thinking=False, stream=True,
        )
        system = params['system']
        cached = [b for b in system if 'cache_control' in b]
        assert len(cached) == ClaudeLLM.MAX_SYSTEM_CACHE_BLOCKS
        assert [b['text'] for b in cached] == ["s1", "s2", "s3"]
        assert system[-1]['text'] == "s4\n\ns5"
        assert 'cache_control' not in system[-1]

    @patch('assistant.llm.ClaudeLLM.config')
    def test_adjacent_markers_skip_empty_cached_block(self, mock_config):
        """Two markers with nothing between them must not emit an empty cached
        block — the API rejects cache_control on empty text."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        sys_msg = "stable<<<CACHE_BREAKPOINT>>>\n\n<<<CACHE_BREAKPOINT>>>tail"
        params = llm._build_api_params(
            [{"role": "system", "content": sys_msg}, {"role": "user", "content": "hi"}],
            thinking=False, stream=True,
        )
        system = params['system']
        assert [b['text'] for b in system] == ["stable", "tail"]
        assert 'cache_control' in system[0]
        assert 'cache_control' not in system[1]

    @patch('assistant.llm.ClaudeLLM.config')
    def test_multiple_markers_stripped_when_prompt_cache_disabled(self, mock_config):
        """With caching off (the prepend path), every marker must be stripped."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM(prompt_cache=False)
        sys_msg = "a<<<CACHE_BREAKPOINT>>>b<<<CACHE_BREAKPOINT>>>c"
        params = llm._build_api_params(
            [{"role": "system", "content": sys_msg}, {"role": "user", "content": "hi"}],
            thinking=False, stream=True,
        )
        assert params['system'] == "abc"

    @patch('assistant.llm.ClaudeLLM.config')
    def test_image_completion_strips_cache_markers_from_system(self, mock_config):
        """The one-shot image path skips _build_cached_system entirely, so it must
        strip the breakpoint markers itself — otherwise they reach the model as
        literal prompt text (the pre-existing leak fixed alongside multi-breakpoint)."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.client = Mock()
        response = Mock()
        response.content = [Mock(type='text', text='ok')]
        llm.client.messages.create.return_value = response

        llm.create_completion_with_images(
            [{"role": "system", "content": "rules\n<<<CACHE_BREAKPOINT>>>\nlive data"},
             {"role": "user", "content": "what's on the camera?"}],
            [b'\x89PNG1234'],
        )

        system = llm.client.messages.create.call_args.kwargs['system']
        assert "<<<CACHE_BREAKPOINT>>>" not in system
        assert "rules" in system and "live data" in system

    @patch('assistant.llm.ClaudeLLM.config')
    def test_image_completion_empty_reply_returns_empty_and_logs_diagnostics(self, mock_config):
        """The Fable empty-reply quirk on image calls: no text blocks must surface as ''
        (so ImageAnalyzer's retry/fallback kicks in) rather than raising, and the
        diagnostic log must carry stop_reason + block types to tell refusal apart
        from the empty-token quirk."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ClaudeLLM import ClaudeLLM
        llm = ClaudeLLM()
        llm.client = Mock()
        response = Mock()
        response.content = [Mock(type='thinking')]
        response.stop_reason = 'end_turn'
        llm.client.messages.create.return_value = response

        with patch('assistant.llm.ClaudeLLM.info') as mock_info:
            result = llm.create_completion_with_images(
                [{"role": "user", "content": "what's in this photo?"}],
                [b'\x89PNG1234'],
            )

        assert result == ''
        logged = " ".join(str(c.args[0]) for c in mock_info.call_args_list)
        assert "stop_reason=end_turn" in logged
        assert "thinking" in logged

    def test_cache_creation_breakdown_splits_5m_and_1h(self):
        """Anthropic's per-TTL split is read through so each write bills at its own rate."""
        from assistant.llm.ClaudeLLM import ClaudeLLM
        usage = Mock(cache_creation=Mock(ephemeral_5m_input_tokens=300,
                                         ephemeral_1h_input_tokens=1200))
        assert ClaudeLLM._cache_creation_breakdown(usage, 1500) == (300, 1200)

    def test_cache_creation_breakdown_falls_back_to_5m_when_detail_absent(self):
        """Without the per-TTL detail (older API / bare mock), all created tokens count as 5m."""
        from assistant.llm.ClaudeLLM import ClaudeLLM
        usage = Mock(spec=['cache_creation_input_tokens'])  # no .cache_creation attr
        assert ClaudeLLM._cache_creation_breakdown(usage, 800) == (800, 0)


# ===========================================================================
# TestGeminiLLM
# ===========================================================================

class TestGeminiLLM:
    @patch('assistant.llm.GeminiLLM.config')
    def test_constructor(self, mock_config):
        mock_config.get_api_key.return_value = "gemini-key"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        assert llm.model == "gemini-3.5-flash"
        assert llm.api_key == "gemini-key"
        assert llm.client is None

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.genai')
    def test_initialize_creates_client(self, mock_genai, mock_config):
        mock_config.get_api_key.return_value = "gemini-key"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.initialize()
        mock_genai.Client.assert_called_once_with(api_key="gemini-key")

    @patch('assistant.llm.GeminiLLM.config')
    def test_name_returns_gemini(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        assert GeminiLLM().name() == "Gemini"

    @patch('assistant.llm.GeminiLLM.config')
    def test_supports_native_thinking_returns_true(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        assert GeminiLLM().supports_native_thinking() is True

    @patch('assistant.llm.GeminiLLM.config')
    def test_does_not_handle_thinking_internally(self, mock_config):
        """Gemini keeps the <thinking>-tag instruction (the voice tier's reasoning path)."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        assert GeminiLLM().handles_thinking_internally() is False

    @patch('assistant.llm.GeminiLLM.config')
    def test_convert_messages_maps_roles(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "response"},
        ]
        sys_instr, converted = llm._convert_messages(messages)
        assert sys_instr == "sys"
        assert len(converted) == 2
        assert converted[0]['role'] == 'user'
        assert converted[1]['role'] == 'model'
        assert converted[0]['parts'] == [{'text': 'hello'}]

    @patch('assistant.llm.GeminiLLM.config')
    def test_convert_messages_strips_cache_markers_from_system(self, mock_config):
        """Regression for the marker leak: Gemini (the voice tier) received Claude's
        <<<CACHE_BREAKPOINT>>> markers as literal system-prompt text. They mean
        nothing to Gemini and must never reach the model."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        sys_instr, _ = GeminiLLM()._convert_messages([
            {"role": "system", "content": "rules<<<CACHE_BREAKPOINT>>>live data"},
            {"role": "user", "content": "hello"},
        ])
        assert sys_instr == "ruleslive data"

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_streaming_completion(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()

        chunk1 = Mock(text="Hello")
        chunk2 = Mock(text="")
        chunk3 = Mock(text=" world")

        llm.client.models.generate_content_stream.return_value = [chunk1, chunk2, chunk3]

        result = list(llm.create_streaming_completion(
            [{"role": "user", "content": "hi"}], thinking=False
        ))
        assert result == ["Hello", " world"]

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_completion_returns_text(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()

        response = Mock(text="full response")
        llm.client.models.generate_content.return_value = response

        result = llm.create_completion([{"role": "user", "content": "hi"}])
        assert result == "full response"

    @patch('assistant.llm.GeminiLLM.config')
    def test_min_thinking_level_respects_model_floor(self, mock_config):
        """gemini-3.7-flash and gemini-3.8-flash reject MINIMAL with 400
        INVALID_ARGUMENT — their floor is LOW. Older Flash generations must
        keep MINIMAL (the voice loop's latency floor)."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        from google.genai.types import ThinkingLevel
        assert GeminiLLM("gemini-3.7-flash")._min_thinking_level() == ThinkingLevel.LOW
        assert GeminiLLM("gemini-3.8-flash")._min_thinking_level() == ThinkingLevel.LOW
        assert GeminiLLM("gemini-3.5-flash")._min_thinking_level() == ThinkingLevel.MINIMAL

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_streaming_37_flash_never_requests_minimal(self, mock_types, mock_config):
        """The non-thinking streaming path is the production voice loop on the
        fast tier — sending MINIMAL to gemini-3.7-flash 400s every request."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        from google.genai.types import ThinkingLevel
        llm = GeminiLLM("gemini-3.7-flash")
        llm.client = Mock()
        llm.client.models.generate_content_stream.return_value = []

        list(llm.create_streaming_completion(
            [{"role": "user", "content": "hi"}], thinking=False
        ))
        assert mock_types.ThinkingConfig.call_args.kwargs['thinking_level'] == ThinkingLevel.LOW

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_streaming_older_flash_keeps_minimal(self, mock_types, mock_config):
        """The floor helper must not blanket-raise every model to LOW — that
        would silently slow the voice loop on models that accept MINIMAL."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        from google.genai.types import ThinkingLevel
        llm = GeminiLLM()
        llm.client = Mock()
        llm.client.models.generate_content_stream.return_value = []

        list(llm.create_streaming_completion(
            [{"role": "user", "content": "hi"}], thinking=False
        ))
        assert mock_types.ThinkingConfig.call_args.kwargs['thinking_level'] == ThinkingLevel.MINIMAL

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_completion_37_flash_never_requests_minimal(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        from google.genai.types import ThinkingLevel
        llm = GeminiLLM("gemini-3.7-flash")
        llm.client = Mock()
        llm.client.models.generate_content.return_value = Mock(text="ok")

        llm.create_completion([{"role": "user", "content": "hi"}], thinking=False)
        assert mock_types.ThinkingConfig.call_args.kwargs['thinking_level'] == ThinkingLevel.LOW

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_streaming_thinking_caps_at_low(self, mock_types, mock_config):
        """Streaming is the voice loop — thinking there is deliberately capped
        at LOW (HIGH/MEDIUM add ~5s, too slow for conversation). The completion
        path's near-identical block uses HIGH, so a copy-paste between them
        would silently regress voice latency."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        from google.genai.types import ThinkingLevel
        llm = GeminiLLM()
        llm.client = Mock()
        llm.client.models.generate_content_stream.return_value = []

        list(llm.create_streaming_completion(
            [{"role": "user", "content": "hi"}], thinking=True
        ))
        assert mock_types.ThinkingConfig.call_args.kwargs['thinking_level'] == ThinkingLevel.LOW

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_completion_thinking_uses_high(self, mock_types, mock_config):
        """Non-streaming completions have no voice-latency constraint — thinking
        must stay HIGH, not drift down to the streaming path's LOW cap."""
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        from google.genai.types import ThinkingLevel
        llm = GeminiLLM()
        llm.client = Mock()
        llm.client.models.generate_content.return_value = Mock(text="ok")

        llm.create_completion([{"role": "user", "content": "hi"}], thinking=True)
        assert mock_types.ThinkingConfig.call_args.kwargs['thinking_level'] == ThinkingLevel.HIGH


# ===========================================================================
# TestGrokLLM
# ===========================================================================

class TestGrokLLM:
    @patch('assistant.llm.ChatGPTLLM.config')
    def test_inherits_chatgpt(self, mock_config):
        mock_config.get_api_key.return_value = "grok-key"
        from assistant.llm.GrokLLM import GrokLLM
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = GrokLLM()
        assert isinstance(llm, ChatGPTLLM)

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_name_returns_grok(self, mock_config):
        mock_config.get_api_key.return_value = "grok-key"
        from assistant.llm.GrokLLM import GrokLLM
        assert GrokLLM().name() == "Grok"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_uses_grok_api_key(self, mock_config):
        mock_config.get_api_key.return_value = "grok-key"
        from assistant.llm.GrokLLM import GrokLLM
        assert GrokLLM().api_key == "grok-key"   # read at use, not captured at construction
        mock_config.get_api_key.assert_called_with('grok')

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_base_url_is_xai(self, mock_config):
        mock_config.get_api_key.return_value = "grok-key"
        from assistant.llm.GrokLLM import GrokLLM
        llm = GrokLLM()
        assert llm.base_url == "https://api.x.ai/v1"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_default_model(self, mock_config):
        mock_config.get_api_key.return_value = "grok-key"
        from assistant.llm.GrokLLM import GrokLLM
        llm = GrokLLM()
        assert llm.model == 'grok-4-1-fast-non-reasoning'

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_custom_model(self, mock_config):
        mock_config.get_api_key.return_value = "grok-key"
        from assistant.llm.GrokLLM import GrokLLM
        llm = GrokLLM(model='grok-custom')
        assert llm.model == 'grok-custom'


# ===========================================================================
# TestOpenRouterLLM
# ===========================================================================

class TestOpenRouterLLM:
    @patch('assistant.llm.ChatGPTLLM.config')
    def test_inherits_chatgpt(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.OpenRouterLLM import OpenRouterLLM
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = OpenRouterLLM(model='test/model')
        assert isinstance(llm, ChatGPTLLM)

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_name_returns_openrouter(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.OpenRouterLLM import OpenRouterLLM
        assert OpenRouterLLM(model='test/model').name() == "OpenRouter"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_uses_openrouter_api_key(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.OpenRouterLLM import OpenRouterLLM
        assert OpenRouterLLM(model='test/model').api_key == "or-key"
        mock_config.get_api_key.assert_called_with('openrouter')

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_base_url_is_openrouter(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.OpenRouterLLM import OpenRouterLLM
        llm = OpenRouterLLM(model='test/model')
        assert llm.base_url == "https://openrouter.ai/api/v1"


# ===========================================================================
# TestQwenLLM
# ===========================================================================

class TestQwenLLM:
    @patch('assistant.llm.ChatGPTLLM.config')
    def test_inherits_openrouter(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.QwenLLM import QwenLLM
        from assistant.llm.OpenRouterLLM import OpenRouterLLM
        llm = QwenLLM()
        assert isinstance(llm, OpenRouterLLM)

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_name_returns_qwen(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.QwenLLM import QwenLLM
        assert QwenLLM().name() == "Qwen"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_default_model(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.QwenLLM import QwenLLM
        llm = QwenLLM()
        assert llm.model == 'qwen/qwen3.8-27b'

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_custom_model(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.QwenLLM import QwenLLM
        llm = QwenLLM(model='qwen/qwen-custom')
        assert llm.model == 'qwen/qwen-custom'


# ===========================================================================
# TestGemmaLLM
# ===========================================================================

class TestGemmaLLM:
    @patch('assistant.llm.ChatGPTLLM.config')
    def test_inherits_openrouter(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.GemmaLLM import GemmaLLM
        from assistant.llm.OpenRouterLLM import OpenRouterLLM
        llm = GemmaLLM()
        assert isinstance(llm, OpenRouterLLM)

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_name_returns_gemma(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.GemmaLLM import GemmaLLM
        assert GemmaLLM().name() == "Gemma"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_default_model(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.GemmaLLM import GemmaLLM
        llm = GemmaLLM()
        assert llm.model == 'google/gemma-4-26b-a4b-it'

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_custom_model(self, mock_config):
        mock_config.get_api_key.return_value = "or-key"
        from assistant.llm.GemmaLLM import GemmaLLM
        llm = GemmaLLM(model='google/gemma-custom')
        assert llm.model == 'google/gemma-custom'


# ===========================================================================
# TestChatGPTLLMImageConversion
# ===========================================================================

class TestChatGPTLLMImageConversion:
    @patch('assistant.llm.ChatGPTLLM.config')
    def test_convert_anthropic_content_with_image_blocks(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc123"}},
                {"type": "text", "text": "What is this?"},
            ]},
        ]
        result = ChatGPTLLM._convert_anthropic_content(messages)
        assert result[0] == messages[0]  # system message unchanged
        user_content = result[1]['content']
        assert user_content[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc123"}}
        assert user_content[1] == {"type": "text", "text": "What is this?"}

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_convert_anthropic_content_passes_string_content_through(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        messages = [
            {"role": "user", "content": "just text"},
        ]
        result = ChatGPTLLM._convert_anthropic_content(messages)
        assert result == messages

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_convert_anthropic_content_png_media_type(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        messages = [
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "pngdata"}},
                {"type": "text", "text": "describe"},
            ]},
        ]
        result = ChatGPTLLM._convert_anthropic_content(messages)
        assert result[0]['content'][0]['image_url']['url'] == "data:image/png;base64,pngdata"

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_streaming_with_image_content_converts_messages(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()

        chunk = Mock()
        chunk.choices = [Mock(delta=Mock(content="I see a cat"))]
        llm.client.chat.completions.create.return_value = [chunk]

        messages = [
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "imgdata"}},
                {"type": "text", "text": "What is this?"},
            ]},
        ]
        result = list(llm.create_streaming_completion(messages))
        assert result == ["I see a cat"]

        call_args = llm.client.chat.completions.create.call_args
        sent_messages = call_args[1]['messages']
        assert sent_messages[0]['content'][0]['type'] == 'image_url'
        assert 'data:image/jpeg;base64,imgdata' in sent_messages[0]['content'][0]['image_url']['url']

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_completion_with_image_content_converts_messages(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        llm = ChatGPTLLM()
        llm.client = Mock()

        response = Mock()
        response.choices = [Mock(message=Mock(content="It's a dog"))]
        llm.client.chat.completions.create.return_value = response

        messages = [
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "imgdata"}},
                {"type": "text", "text": "What is this?"},
            ]},
        ]
        result = llm.create_completion(messages)
        assert result == "It's a dog"

        call_args = llm.client.chat.completions.create.call_args
        sent_messages = call_args[1]['messages']
        assert sent_messages[0]['content'][0]['type'] == 'image_url'


# ===========================================================================
# TestGeminiLLMImageConversion
# ===========================================================================

class TestGeminiLLMImageConversion:
    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_convert_messages_with_image_blocks(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        mock_types.Part.from_bytes.return_value = "mock_image_part"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()

        img_data = base64.b64encode(b"fake image").decode()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": "What is this?"},
            ]},
        ]
        sys_instr, converted = llm._convert_messages(messages)
        assert sys_instr == "sys"
        assert len(converted) == 1
        assert converted[0]['role'] == 'user'
        assert converted[0]['parts'][0] == "mock_image_part"
        assert converted[0]['parts'][1] == {'text': 'What is this?'}
        mock_types.Part.from_bytes.assert_called_once()

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_convert_messages_string_content_unchanged(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        _, converted = llm._convert_messages(messages)
        assert converted[0]['parts'] == [{'text': 'hello'}]
        assert converted[1]['role'] == 'model'
        assert converted[1]['parts'] == [{'text': 'hi there'}]

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_streaming_with_image_content(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        mock_types.Part.from_bytes.return_value = "mock_part"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()

        chunk = Mock(text="I see an image")
        llm.client.models.generate_content_stream.return_value = [chunk]

        img_data = base64.b64encode(b"fake").decode()
        messages = [
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": "describe"},
            ]},
        ]
        result = list(llm.create_streaming_completion(messages))
        assert result == ["I see an image"]

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_create_completion_with_images(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        mock_types.Part.from_bytes.return_value = "mock_image_part"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()

        response = Mock(text="I see a cat")
        llm.client.models.generate_content.return_value = response

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "What is in this image?"},
        ]
        png_bytes = b'\x89PNG' + b'\x00' * 10
        result = llm.create_completion_with_images(messages, [png_bytes])
        assert result == "I see a cat"
        mock_types.Part.from_bytes.assert_called_once_with(data=png_bytes, mime_type="image/png")


# ===========================================================================
# TestGeminiLLMVideo
# ===========================================================================

class TestGeminiLLMVideo:
    @patch('assistant.llm.GeminiLLM.config')
    def test_supports_video(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        assert GeminiLLM().supports_video() is True

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_think_with_video_sends_inline_clip_before_text(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        mock_types.Part.return_value = "video_part"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM(model="gemini-video")
        llm.client = Mock()
        llm.client.models.generate_content.return_value = Mock(text="A dog runs", usage_metadata=None)

        result = llm.think_with_video("sys<<<CACHE_BREAKPOINT>>>tem", "describe", b"vid", "video/quicktime")

        assert result == "A dog runs"
        mock_types.Blob.assert_called_once_with(data=b"vid", mime_type="video/quicktime")
        mock_types.Part.assert_called_once_with(inline_data=mock_types.Blob.return_value, video_metadata=None)
        mock_types.VideoMetadata.assert_not_called()
        mock_types.GenerateContentConfig.assert_called_once_with(system_instruction="system")
        call = llm.client.models.generate_content.call_args[1]
        assert call["model"] == "gemini-video"
        assert call["contents"] == [{'role': 'user', 'parts': ["video_part", {'text': "describe"}]}]

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_think_with_video_passes_fps_as_video_metadata(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()
        llm.client.models.generate_content.return_value = Mock(text="ok", usage_metadata=None)

        llm.think_with_video("sys", "user", b"vid", "video/mp4", fps=0.5)

        mock_types.VideoMetadata.assert_called_once_with(fps=0.5)
        mock_types.Part.assert_called_once_with(
            inline_data=mock_types.Blob.return_value, video_metadata=mock_types.VideoMetadata.return_value)

    @staticmethod
    def _uploading_llm(mock_config, states=("ACTIVE",)):
        """A GeminiLLM whose files.upload returns a file in states[0] and files.get walks the rest."""
        from assistant.llm.GeminiLLM import GeminiLLM
        mock_config.get_api_key.return_value = "k"
        llm = GeminiLLM(model="gemini-video")
        llm.client = Mock()
        files = []
        for state in states:
            f = Mock(uri="https://files/abc")
            f.name = "files/abc"
            f.state = Mock()
            f.state.name = state
            files.append(f)
        llm.client.files.upload.return_value = files[0]
        llm.client.files.get.side_effect = files[1:]
        llm.client.models.generate_content.return_value = Mock(text="A long clip", usage_metadata=None)
        return llm

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_large_clip_goes_through_the_files_api(self, mock_types, mock_config):
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT
        llm = self._uploading_llm(mock_config)
        clip = b"v" * (INLINE_VIDEO_LIMIT + 1)

        result = llm.think_with_video("sys", "describe", clip, "video/mp4")

        assert result == "A long clip"
        upload = llm.client.files.upload.call_args.kwargs
        assert upload["file"].read() == clip
        mock_types.UploadFileConfig.assert_called_once_with(mime_type="video/mp4")
        mock_types.FileData.assert_called_once_with(file_uri="https://files/abc", mime_type="video/mp4")
        mock_types.Part.assert_called_once_with(file_data=mock_types.FileData.return_value, video_metadata=None)
        mock_types.Blob.assert_not_called()
        llm.client.files.delete.assert_called_once_with(name="files/abc")

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_clip_at_the_inline_limit_stays_inline(self, mock_types, mock_config):
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT
        llm = self._uploading_llm(mock_config)

        llm.think_with_video("sys", "describe", b"v" * INLINE_VIDEO_LIMIT, "video/mp4")

        llm.client.files.upload.assert_not_called()
        mock_types.Blob.assert_called_once()

    @patch('assistant.llm.GeminiLLM.time.sleep')
    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_upload_waits_for_processing_to_finish(self, mock_types, mock_config, sleep):
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT
        llm = self._uploading_llm(mock_config, states=("PROCESSING", "PROCESSING", "ACTIVE"))

        assert llm.think_with_video("sys", "describe", b"v" * (INLINE_VIDEO_LIMIT + 1), "video/mp4") == "A long clip"

        assert llm.client.files.get.call_count == 2
        assert sleep.call_count == 2
        llm.client.files.delete.assert_called_once_with(name="files/abc")

    @patch('assistant.llm.AbstractLLM.time.sleep')
    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_failed_processing_raises_and_deletes(self, mock_types, mock_config, sleep):
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT
        llm = self._uploading_llm(mock_config, states=("FAILED",))

        with pytest.raises(RuntimeError, match="state FAILED"):
            llm.think_with_video("sys", "describe", b"v" * (INLINE_VIDEO_LIMIT + 1), "video/mp4")

        llm.client.models.generate_content.assert_not_called()
        assert llm.client.files.delete.call_count == 3  # once per retry attempt

    @patch('assistant.llm.GeminiLLM.time.monotonic')
    @patch('assistant.llm.GeminiLLM.time.sleep')
    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_processing_timeout_raises_and_deletes(self, mock_types, mock_config, sleep, monotonic):
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT, FILE_PROCESSING_TIMEOUT
        llm = self._uploading_llm(mock_config, states=("PROCESSING",) * 10)
        # The patch replaces the global time.monotonic, so daemon threads can consume
        # entries too: a finite list ran dry under the full suite (StopIteration).
        monotonic.side_effect = itertools.chain([0, 1, 1], itertools.repeat(FILE_PROCESSING_TIMEOUT + 2))

        with pytest.raises(TimeoutError, match="still processing"):
            llm._create_completion_with_video("sys", "describe", b"v" * (INLINE_VIDEO_LIMIT + 1), "video/mp4", None)

        llm.client.files.delete.assert_called_once_with(name="files/abc")

    @patch('assistant.llm.AbstractLLM.time.sleep')
    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_uploaded_file_is_deleted_when_generation_fails(self, mock_types, mock_config, sleep):
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT
        llm = self._uploading_llm(mock_config)
        llm.client.models.generate_content.side_effect = RuntimeError("503")

        with pytest.raises(RuntimeError):
            llm.think_with_video("sys", "describe", b"v" * (INLINE_VIDEO_LIMIT + 1), "video/mp4")

        assert llm.client.files.delete.call_count == 3

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_failed_cleanup_does_not_lose_the_description(self, mock_types, mock_config):
        """The delete is best effort (files expire on their own); a hiccup there must not
        turn a finished, paid-for analysis into a retry."""
        from assistant.llm.GeminiLLM import INLINE_VIDEO_LIMIT
        llm = self._uploading_llm(mock_config)
        llm.client.files.delete.side_effect = RuntimeError("404 already gone")

        assert llm.think_with_video("sys", "describe", b"v" * (INLINE_VIDEO_LIMIT + 1), "video/mp4") == "A long clip"
        assert llm.client.models.generate_content.call_count == 1

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_empty_response_returns_empty_string_instead_of_none(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()
        blocked = Mock(text=None, usage_metadata=None, prompt_feedback=Mock(block_reason="SAFETY"))
        blocked.candidates = []
        llm.client.models.generate_content.return_value = blocked

        assert llm.think_with_video("sys", "user", b"vid", "video/mp4") == ""
        assert llm.client.models.generate_content.call_count == 1

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_empty_image_response_returns_empty_string_instead_of_none(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()
        blocked = Mock(text=None, usage_metadata=None, prompt_feedback=Mock(block_reason="SAFETY"))
        blocked.candidates = []
        llm.client.models.generate_content.return_value = blocked

        assert llm.think("sys", "describe", images=[b'\xff\xd8\xff' + b'\x00' * 10]) == ""

    @patch('assistant.llm.GeminiLLM.config')
    def test_max_video_bytes_is_the_files_api_limit(self, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM, MAX_VIDEO_BYTES
        assert GeminiLLM().max_video_bytes() == MAX_VIDEO_BYTES == 2 * 1024 ** 3

    @patch('assistant.llm.GeminiLLM.config')
    @patch('assistant.llm.GeminiLLM.types')
    def test_think_with_video_retries_transient_failure(self, mock_types, mock_config):
        mock_config.get_api_key.return_value = "k"
        from assistant.llm.GeminiLLM import GeminiLLM
        llm = GeminiLLM()
        llm.client = Mock()
        llm.client.models.generate_content.side_effect = [RuntimeError("503"), Mock(text="ok", usage_metadata=None)]

        with patch('assistant.llm.AbstractLLM.time.sleep'):
            assert llm.think_with_video("sys", "user", b"vid", "video/mp4") == "ok"
        assert llm.client.models.generate_content.call_count == 2


# ===========================================================================
# TestOllamaLLM
# ===========================================================================

class TestOllamaLLM:
    def _make_llm(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        return OllamaLLM(model='gemma4:26b', base_url='http://localhost:11434')

    @patch('assistant.llm.OllamaLLM.config')
    def test_constructor_defaults(self, mock_config):
        mock_config.get.side_effect = lambda key, default=None: default
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = OllamaLLM()
        assert llm.model == 'gemma4:26b'
        assert llm.base_url == 'http://localhost:11434'
        assert llm.think is False
        assert llm.client is True

    @patch('assistant.llm.OllamaLLM.config')
    def test_base_url_from_config(self, mock_config):
        mock_config.get.side_effect = \
            lambda key, default=None: 'http://studio:11434' if key == 'ollama.base_url' else default
        from assistant.llm.OllamaLLM import OllamaLLM
        try:
            assert OllamaLLM().base_url == 'http://studio:11434'
        finally:
            OllamaLLM._ssh_log = None

    @patch('assistant.llm.OllamaLLM.config')
    def test_explicit_base_url_wins_over_config(self, mock_config):
        mock_config.get.side_effect = \
            lambda key, default=None: 'http://studio:11434' if key == 'ollama.base_url' else default
        from assistant.llm.OllamaLLM import OllamaLLM
        try:
            assert OllamaLLM(base_url='http://other:11434').base_url == 'http://other:11434'
        finally:
            OllamaLLM._ssh_log = None

    @patch('assistant.llm.OllamaLLM.trace')
    def test_config_trace_logged_once_per_model_and_base_url(self, mock_trace):
        # The handler pool constructs dozens of identical instances (25 handlers
        # x 2 tiers), which flooded startup with ~50 identical config lines.
        # The line must appear once per distinct (model, base_url) — repeated
        # instances stay silent, a genuinely new config still logs.
        from assistant.llm.OllamaLLM import OllamaLLM
        saved = OllamaLLM._logged_configs
        OllamaLLM._logged_configs = set()
        try:
            for _ in range(3):
                OllamaLLM(model='gemma4:26b', base_url='http://localhost:11434')
            OllamaLLM(model='qwen3.8:27b', base_url='http://localhost:11434')
            config_lines = [c for c in mock_trace.call_args_list
                            if 'using base_url' in str(c)]
            assert len(config_lines) == 2
        finally:
            OllamaLLM._logged_configs = saved

    def test_name_returns_ollama(self):
        assert self._make_llm().name() == "Ollama"

    def test_build_request(self):
        llm = self._make_llm()
        req = llm._build_request([{"role": "user", "content": "hi"}], stream=True)
        assert req == {
            "model": "gemma4:26b",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "keep_alive": -1,
            "think": False,
        }

    def test_build_request_with_think_enabled(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = OllamaLLM(think=True)
        req = llm._build_request([], stream=False)
        assert req["think"] is True

    def test_build_request_flattens_anthropic_image_blocks(self):
        # Ollama's /api/chat rejects list content with a 400. A message whose
        # content is a list of Anthropic-style blocks (e.g. camera snapshots)
        # must become string content plus a message-level `images` list of raw
        # base64 — otherwise the whole request fails (the real-world 400 bug).
        llm = self._make_llm()
        b64 = base64.b64encode(b"jpegbytes").decode()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Camera: Front door"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": "Camera: Garden"},
            ],
        }]
        req = llm._build_request(messages, stream=True)
        msg = req["messages"][0]
        assert msg["content"] == "Camera: Front door\nCamera: Garden"
        assert msg["images"] == [b64]

    def test_build_request_string_content_passes_through(self):
        # String content must be left exactly as-is, with no spurious `images` key.
        llm = self._make_llm()
        req = llm._build_request([{"role": "user", "content": "hi"}], stream=True)
        assert req["messages"] == [{"role": "user", "content": "hi"}]
        assert "images" not in req["messages"][0]

    def test_build_request_strips_cache_markers_from_system(self):
        # Regression for the marker leak: Ollama received Claude's cache breakpoint
        # markers as literal system-prompt text. Stripping is deterministic, so
        # Ollama's own KV-cache prefix matching is unaffected.
        llm = self._make_llm()
        req = llm._build_request([
            {"role": "system", "content": "rules<<<CACHE_BREAKPOINT>>>live data"},
            {"role": "user", "content": "hi"},
        ], stream=False)
        assert req["messages"][0]["content"] == "ruleslive data"
        assert req["messages"][1] == {"role": "user", "content": "hi"}

    def test_build_request_collects_multiple_image_blocks(self):
        # The real-world bug surfaced with camera snapshots (plural). A regression
        # that kept only the first image would still pass the single-image test
        # above, so pin the multi-image contract explicitly: every image block's
        # base64 must reach Ollama's message-level `images` list, in order.
        llm = self._make_llm()
        front_b64 = base64.b64encode(b"pool_jpeg").decode()
        garden_b64 = base64.b64encode(b"altan_jpeg").decode()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Cameras:"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": front_b64}},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": garden_b64}},
            ],
        }]
        req = llm._build_request(messages, stream=True)
        assert req["messages"][0]["images"] == [front_b64, garden_b64]

    def test_build_request_caps_tokens_for_pre_heat(self):
        # num_predict is what turns a request into a pre-heat: generate just one
        # token to warm the KV cache instead of a full ~50s completion. A real
        # request passes None and must NOT carry num_predict (test_build_request
        # pins that). Here we pin the pre-heat side so the cap can't silently drop.
        llm = self._make_llm()
        req = llm._build_request([{"role": "user", "content": "hi"}], stream=True, num_predict=1)
        assert req["options"] == {"num_predict": 1}

    def test_build_request_qwen38_non_thinking_overrides_samplers(self):
        # Qwen3.8's modelfile bakes in thinking-mode samplers (temp 1.0, top_p 0.95),
        # which make non-thinking output rambly and repetitive. With think=False the
        # request must carry Qwen's recommended non-thinking samplers instead.
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = OllamaLLM(model='qwen3.8:27b', base_url='http://localhost:11434')
        req = llm._build_request([{"role": "user", "content": "hi"}], stream=True)
        assert req["options"] == {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 1.5,
        }

    def test_build_request_qwen38_thinking_keeps_baked_in_samplers(self):
        # With thinking enabled the modelfile's own samplers are correct — the
        # override must NOT fire, so no options key at all.
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = OllamaLLM(model='qwen3.8:27b', base_url='http://localhost:11434', think=True)
        req = llm._build_request([{"role": "user", "content": "hi"}], stream=True)
        assert "options" not in req

    def test_build_request_qwen38_pre_heat_merges_num_predict_into_samplers(self):
        # num_predict used to be assigned as the whole options dict; the qwen
        # override turned that into a merge. Pin that a qwen3.8 pre-heat carries
        # BOTH — dropping the samplers or the token cap would each regress silently.
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = OllamaLLM(model='qwen3.8:27b', base_url='http://localhost:11434')
        req = llm._build_request([{"role": "user", "content": "hi"}], stream=True, num_predict=1)
        assert req["options"] == {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 1.5,
            "num_predict": 1,
        }

    # --- pre-heat / render HTTP timeouts --------------------------------
    # Pre-heats hold _pre_heat_in_flight; a hung socket to a dead remote host
    # (ollama.base_url on another machine) with no timeout would keep the flag
    # set forever, silently disabling every future pre-heat. Real requests
    # deliberately carry NO timeout — a slow generation must never be cut off.

    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_rendered_prompt_post_carries_timeout(self, mock_post):
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = self._make_llm()
        mock_post.return_value.json.return_value = {"_debug_info": {"rendered_template": "x"}}
        assert llm._rendered_prompt([{"role": "user", "content": "hi"}]) == "x"
        assert mock_post.call_args.kwargs["timeout"] == OllamaLLM._RENDER_TIMEOUT

    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_raw_pre_heat_post_carries_timeout(self, mock_post):
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = self._make_llm()
        rendered = "system prefix<|im_end|>\n<|im_start|>assistant\n<think>"
        with patch.object(llm, '_rendered_prompt', return_value=rendered):
            assert llm._pre_heat_raw_truncated([{"role": "user", "content": "hi"}]) is True
        assert mock_post.call_args.kwargs["timeout"] == OllamaLLM._PRE_HEAT_TIMEOUT

    @patch('assistant.llm.OllamaLLM.OllamaLLM._rendered_prompt', return_value=None)
    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_streaming_timeout_only_for_pre_heat(self, mock_post, mock_rendered):
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = self._make_llm()
        mock_post.return_value.iter_lines.return_value = []
        list(llm.create_streaming_completion([{"role": "user", "content": "hi"}], num_predict=1))
        assert mock_post.call_args.kwargs["timeout"] == OllamaLLM._PRE_HEAT_TIMEOUT
        list(llm.create_streaming_completion([{"role": "user", "content": "hi"}]))
        assert mock_post.call_args.kwargs["timeout"] is None

    def test_streaming_raises_on_thinking(self):
        llm = self._make_llm()
        with pytest.raises(ThinkingNotSupportedError):
            list(llm.create_streaming_completion([], thinking=True))

    def test_completion_raises_on_thinking(self):
        llm = self._make_llm()
        with pytest.raises(ThinkingNotSupportedError):
            llm.create_completion([], thinking=True)

    def test_prefers_prepended_user_query_false(self):
        llm = self._make_llm()
        assert llm.prefers_prepended_user_query() is False

    @patch('assistant.llm.OllamaLLM.OllamaLLM._rendered_prompt', return_value=None)
    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_create_completion(self, mock_post, mock_rendered):
        llm = self._make_llm()
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "message": {"content": "hello back"},
            "done": True,
            "prompt_eval_count": 10, "prompt_eval_duration": 1_000_000_000,
            "eval_count": 5, "eval_duration": 500_000_000,
            "total_duration": 1_500_000_000,
        }
        mock_post.return_value = mock_resp

        result = llm.create_completion([{"role": "user", "content": "hi"}])
        assert result == "hello back"
        mock_resp.raise_for_status.assert_called_once()

    @patch('assistant.llm.OllamaLLM.OllamaLLM._rendered_prompt', return_value=None)
    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_create_streaming_completion(self, mock_post, mock_rendered):
        import json as _json
        llm = self._make_llm()

        lines = [
            _json.dumps({"message": {"content": "Hello"}, "done": False}).encode(),
            _json.dumps({"message": {"content": " world"}, "done": False}).encode(),
            b"",
            _json.dumps({"message": {"content": ""}, "done": True,
                         "prompt_eval_count": 10, "prompt_eval_duration": 1e9,
                         "eval_count": 5, "eval_duration": 5e8,
                         "total_duration": 1.5e9}).encode(),
        ]
        mock_resp = Mock()
        mock_resp.iter_lines.return_value = lines
        mock_post.return_value = mock_resp

        result = list(llm.create_streaming_completion([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " world"]

    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_create_completion_with_images_bytes(self, mock_post):
        llm = self._make_llm()
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "message": {"content": "I see a photo"},
            "done": True,
            "prompt_eval_count": 50, "prompt_eval_duration": 2e9,
            "eval_count": 10, "eval_duration": 1e9,
            "total_duration": 3e9,
        }
        mock_post.return_value = mock_resp

        msgs = [{"role": "user", "content": "what is this?"}]
        result = llm.create_completion_with_images(msgs, [b'\x89PNG\x00\x00'])
        assert result == "I see a photo"

        call_json = mock_post.call_args[1]['json']
        assert 'images' in call_json['messages'][0]

    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_create_completion_with_images_base64_string(self, mock_post):
        llm = self._make_llm()
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "message": {"content": "nice"}, "done": True,
            "prompt_eval_count": 10, "prompt_eval_duration": 1e9,
            "eval_count": 5, "eval_duration": 5e8, "total_duration": 1.5e9,
        }
        mock_post.return_value = mock_resp

        b64 = base64.b64encode(b"test image").decode()
        result = llm.create_completion_with_images(
            [{"role": "user", "content": "describe"}], [b64]
        )
        assert result == "nice"
        sent_images = mock_post.call_args[1]['json']['messages'][0]['images']
        assert sent_images == [b64]

    @patch('assistant.llm.OllamaLLM.requests.post')
    def test_create_completion_with_images_routes_through_build_request(self, mock_post):
        # ea11de61 replaced the inline payload in create_completion_with_images
        # with self._build_request(...) so vision requests carry the same fields
        # as every other request. The visible behavioural side-effect is that
        # `think` now flows through — a revert to the inline dict would silently
        # drop it, and the existing image tests wouldn't notice. Pin the
        # construction contract on the vision path.
        from assistant.llm.OllamaLLM import OllamaLLM
        llm = OllamaLLM(model='gemma4:26b', base_url='http://localhost:11434', think=True)
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "message": {"content": "ok"}, "done": True,
            "prompt_eval_count": 1, "prompt_eval_duration": 1e9,
            "eval_count": 1, "eval_duration": 1e9, "total_duration": 2e9,
        }
        mock_post.return_value = mock_resp

        llm.create_completion_with_images(
            [{"role": "user", "content": "what?"}], [b'\x89PNG\x00\x00']
        )

        call_json = mock_post.call_args[1]['json']
        assert call_json["model"] == "gemma4:26b"
        assert call_json["stream"] is False
        assert call_json["keep_alive"] == -1
        assert call_json["think"] is True

    def test_read_cache_metrics_parses_log(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        log_file = tmp_path / "ollama.log"
        log_file.write_text(
            'time=... level=DEBUG source=cache.go:151 msg="loading cache slot" '
            'id=0 cache=6533 prompt=6344 used=5786 remaining=558\n'
        )
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._prefill_log_start = 0
            result = OllamaLLM._read_cache_metrics()
            assert result == (5786, 558)
        finally:
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_read_cache_metrics_returns_none_when_no_file(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(tmp_path / "nonexistent.log")
            OllamaLLM._prefill_log_start = 0
            assert OllamaLLM._read_cache_metrics() is None
        finally:
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_read_cache_metrics_returns_none_when_no_new_content(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        log_file = tmp_path / "ollama.log"
        log_file.write_text("some old content\n")
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._prefill_log_start = log_file.stat().st_size
            assert OllamaLLM._read_cache_metrics() is None
        finally:
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_read_cache_metrics_is_idempotent_across_polls(self, tmp_path):
        # Regression: the read must NOT advance a shared cursor. The render thread
        # polls this many times per second; every poll must return the same tuple,
        # never consume the line so a later read (or done-time log) sees None.
        from assistant.llm.OllamaLLM import OllamaLLM
        log_file = tmp_path / "ollama.log"
        log_file.write_text(
            'msg="loading cache slot" id=0 cache=6533 prompt=6344 used=5786 remaining=558\n'
        )
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._prefill_log_start = 0
            assert OllamaLLM._read_cache_metrics() == (5786, 558)
            assert OllamaLLM._read_cache_metrics() == (5786, 558)
            assert OllamaLLM._read_cache_metrics() == (5786, 558)
        finally:
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_read_cache_metrics_recovers_from_partial_line(self, tmp_path):
        # Regression (stuck-at-0%): a poll landing mid-write sees a partial line
        # with no remaining= yet. It must return None and leave the bytes for the
        # next poll to re-read once the line is complete — never discard them.
        from assistant.llm.OllamaLLM import OllamaLLM
        log_file = tmp_path / "ollama.log"
        log_file.write_text('msg="loading cache slot" id=0 cache=6533 prompt=6344 used=5786 ')
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._prefill_log_start = 0
            assert OllamaLLM._read_cache_metrics() is None  # partial line, no match
            with open(log_file, 'a') as f:
                f.write('remaining=558\n')
            assert OllamaLLM._read_cache_metrics() == (5786, 558)  # re-read, now complete
        finally:
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_read_cache_metrics_excludes_lines_before_anchor(self, tmp_path):
        # Regression (stale latching): a cache-slot line from a prior request or a
        # preheat sits below the anchor and must be ignored; the first line AFTER
        # the anchor is the one for the current request.
        from assistant.llm.OllamaLLM import OllamaLLM
        log_file = tmp_path / "ollama.log"
        log_file.write_text(
            'msg="loading cache slot" id=0 cache=1 prompt=1 used=111 remaining=222\n'
        )
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._prefill_log_start = log_file.stat().st_size  # anchor past stale line
            with open(log_file, 'a') as f:
                f.write('msg="loading cache slot" id=0 cache=2 prompt=2 used=5786 remaining=558\n')
            assert OllamaLLM._read_cache_metrics() == (5786, 558)
        finally:
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_begin_prefill_anchors_at_current_log_eof(self, tmp_path):
        # _begin_prefill is called before the POST; the cache-slot line Ollama
        # logs afterwards is what we want. Verify the anchor lands at EOF so a
        # pre-existing (prior-request) line is excluded but the new one is found.
        from assistant.llm.OllamaLLM import OllamaLLM
        log_file = tmp_path / "ollama.log"
        log_file.write_text(
            'msg="loading cache slot" id=0 cache=1 prompt=1 used=999 remaining=999\n'
        )
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._begin_prefill()  # anchors at current EOF (past the stale line)
            assert OllamaLLM._prefill_log_start == log_file.stat().st_size
            with open(log_file, 'a') as f:
                f.write('msg="loading cache slot" id=0 cache=2 prompt=2 used=5786 remaining=558\n')
            assert OllamaLLM._consume_prefill_metrics() == (5786, 558)
        finally:
            OllamaLLM._end_prefill()
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_consume_prefill_metrics_parses_log_once_then_caches(self):
        # prefill_progress() is polled many times per second by the render thread.
        # _consume_prefill_metrics parses the log only on the first successful poll
        # and serves a cached value thereafter — an optimization that avoids
        # re-reading the log file every render frame once the line is found.
        from assistant.llm.OllamaLLM import OllamaLLM
        with patch.object(OllamaLLM, '_read_cache_metrics',
                          side_effect=[(5786, 558), None, None]) as mock_read:
            OllamaLLM._begin_prefill()  # resets _prefill_cache_metrics to None
            try:
                assert OllamaLLM._consume_prefill_metrics() == (5786, 558)
                # Subsequent polls return the cached tuple and never re-read the log.
                assert OllamaLLM._consume_prefill_metrics() == (5786, 558)
                assert OllamaLLM._consume_prefill_metrics() == (5786, 558)
                mock_read.assert_called_once()
            finally:
                OllamaLLM._end_prefill()

    def test_consume_prefill_metrics_throttles_rereads_until_line_appears(self):
        # Before the cache-slot line lands in the log, every poll used to re-read
        # it — over ssh that's a subprocess per TUI render frame. Failed reads
        # are now throttled to _PROGRESS_POLL_INTERVAL_S; force=True (the
        # done-time read in _log_metrics) bypasses the throttle.
        from assistant.llm.OllamaLLM import OllamaLLM
        with patch.object(OllamaLLM, '_read_cache_metrics', return_value=None) as mock_read:
            OllamaLLM._begin_prefill()
            try:
                assert OllamaLLM._consume_prefill_metrics() is None  # first poll reads
                assert OllamaLLM._consume_prefill_metrics() is None  # throttled
                assert OllamaLLM._consume_prefill_metrics() is None  # throttled
                mock_read.assert_called_once()
                assert OllamaLLM._consume_prefill_metrics(force=True) is None
                assert mock_read.call_count == 2
            finally:
                OllamaLLM._end_prefill()

    def test_prefill_log_reads_run_outside_prefill_lock(self):
        # _mark_first_token takes _prefill_lock per streamed token and the TUI
        # render thread takes it per frame. Over ssh a log read can take seconds,
        # so holding the lock across it would freeze token delivery and the TUI.
        # Pin that both readers are invoked with the lock free: a non-blocking
        # acquire from inside the (patched) reader must succeed.
        from assistant.llm.OllamaLLM import OllamaLLM

        def assert_lock_free_and_return(value):
            def _reader():
                acquired = OllamaLLM._prefill_lock.acquire(blocking=False)
                assert acquired, "log read must not run under _prefill_lock"
                OllamaLLM._prefill_lock.release()
                return value
            return _reader

        with patch.object(OllamaLLM, '_read_cache_metrics',
                          side_effect=assert_lock_free_and_return((100, 50))), \
             patch.object(OllamaLLM, '_read_prefill_progress',
                          side_effect=assert_lock_free_and_return((2048, 17531))):
            OllamaLLM._begin_prefill()
            try:
                assert OllamaLLM._consume_prefill_metrics() == (100, 50)
                assert OllamaLLM._poll_prefill_progress() == (2048, 17531)
            finally:
                OllamaLLM._end_prefill()

    def test_prefill_progress_none_when_idle(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        OllamaLLM._end_prefill()
        assert OllamaLLM.prefill_progress() is None

    def test_prefill_progress_zero_before_cache_line(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            # Log present but no "loading cache slot" line yet at request start.
            log_file = tmp_path / "ollama.log"
            log_file.write_text("nothing useful here\n")
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._begin_prefill()
            assert OllamaLLM.prefill_progress() == 0.0
        finally:
            OllamaLLM._end_prefill()
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_prefill_progress_full_cache_hit_is_complete(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            log_file = tmp_path / "ollama.log"
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._begin_prefill()  # anchors before Ollama logs the line
            log_file.write_text(
                'msg="loading cache slot" id=0 cache=100 prompt=100 used=100 remaining=0\n'
            )
            assert OllamaLLM.prefill_progress() == 100.0
        finally:
            OllamaLLM._end_prefill()
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    def test_prefill_progress_estimates_from_elapsed(self, tmp_path):
        import time
        from assistant.llm.OllamaLLM import OllamaLLM
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        original_tps = OllamaLLM._prefill_tps
        try:
            log_file = tmp_path / "ollama.log"
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._prefill_tps = 1000.0  # 1000 tokens @ 1000 t/s => 1.0s eta
            OllamaLLM._begin_prefill()
            log_file.write_text(
                'msg="loading cache slot" id=0 cache=0 prompt=1000 used=0 remaining=1000\n'
            )
            OllamaLLM._prefill_start_ts = time.monotonic() - 0.5  # half the eta elapsed
            pct = OllamaLLM.prefill_progress()
            assert 40.0 < pct < 60.0
        finally:
            OllamaLLM._end_prefill()
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor
            OllamaLLM._prefill_tps = original_tps

    def test_prefill_progress_capped_until_first_token(self, tmp_path):
        import time
        from assistant.llm.OllamaLLM import OllamaLLM
        original_path = OllamaLLM._ollama_log_path
        original_anchor = OllamaLLM._prefill_log_start
        try:
            log_file = tmp_path / "ollama.log"
            OllamaLLM._ollama_log_path = str(log_file)
            OllamaLLM._begin_prefill()
            log_file.write_text(
                'msg="loading cache slot" id=0 cache=0 prompt=1000 used=0 remaining=1000\n'
            )
            OllamaLLM._prefill_start_ts = time.monotonic() - 9999  # way past eta
            assert OllamaLLM.prefill_progress() == 99.0
            OllamaLLM._mark_first_token()
            assert OllamaLLM.prefill_progress() == 100.0
        finally:
            OllamaLLM._end_prefill()
            OllamaLLM._ollama_log_path = original_path
            OllamaLLM._prefill_log_start = original_anchor

    # --- prefill-rate self-calibration (EMA) ----------------------------
    # The progress estimate's accuracy hinges on _prefill_tps, which
    # _log_metrics refines via an EMA from each completed request's observed
    # prefill rate. Near-full cache hits (tiny `remaining` over a tiny
    # duration) yield a noisy rate and are deliberately skipped so they don't
    # corrupt the estimate that drives the KV-Cache bar.

    def test_log_metrics_refines_prefill_rate_via_ema(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        original_tps = OllamaLLM._prefill_tps
        original_metrics = OllamaLLM._prefill_cache_metrics
        try:
            # remaining=558 over 1.0s => observed 558 t/s; EMA blends 50/50.
            OllamaLLM._prefill_cache_metrics = (5000, 558)
            OllamaLLM._prefill_tps = 350.0
            self._make_llm()._log_metrics({
                "prompt_eval_count": 5558, "prompt_eval_duration": 1_000_000_000,
                "eval_count": 5, "eval_duration": 500_000_000,
                "total_duration": 1_500_000_000,
            })
            assert OllamaLLM._prefill_tps == pytest.approx(0.5 * 350.0 + 0.5 * 558.0)
        finally:
            OllamaLLM._prefill_tps = original_tps
            OllamaLLM._prefill_cache_metrics = original_metrics

    def test_log_metrics_skips_rate_update_on_near_full_cache_hit(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        original_tps = OllamaLLM._prefill_tps
        original_metrics = OllamaLLM._prefill_cache_metrics
        try:
            # remaining=50 (<=100) is a near-full hit — too noisy to learn from.
            OllamaLLM._prefill_cache_metrics = (5000, 50)
            OllamaLLM._prefill_tps = 350.0
            self._make_llm()._log_metrics({
                "prompt_eval_count": 5050, "prompt_eval_duration": 1_000_000_000,
                "eval_count": 5, "eval_duration": 500_000_000,
                "total_duration": 1_500_000_000,
            })
            assert OllamaLLM._prefill_tps == 350.0
        finally:
            OllamaLLM._prefill_tps = original_tps
            OllamaLLM._prefill_cache_metrics = original_metrics

    def test_log_prefix_match_first_request(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        original = OllamaLLM._last_payload
        try:
            OllamaLLM._last_payload = ""
            messages = [{"role": "user", "content": "hello"}]
            payload_str = OllamaLLM._log_prefix_match(messages)
            assert OllamaLLM._last_payload == "", "_log_prefix_match should not update _last_payload"
            OllamaLLM._mark_payload_completed(payload_str)
            assert OllamaLLM._last_payload != ""
        finally:
            OllamaLLM._last_payload = original

    def test_log_prefix_match_computes_shared_chars(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        original = OllamaLLM._last_payload
        try:
            OllamaLLM._last_payload = ""
            msgs1 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
            payload_str = OllamaLLM._log_prefix_match(msgs1)
            OllamaLLM._mark_payload_completed(payload_str)
            msgs2 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "world"}]
            OllamaLLM._log_prefix_match(msgs2)
            assert OllamaLLM._last_payload != ""
        finally:
            OllamaLLM._last_payload = original

    # --- pre-heat in-flight guard ---------------------------------------
    # Ollama processes one request at a time. The guard ensures at most one
    # pre-heat is ever fired; a newer prompt arriving mid-flight is stored and
    # fired when the current one completes (rather than piling up requests).

    def _reset_preheat_state(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        OllamaLLM._pre_heat_in_flight = False
        OllamaLLM._pre_heat_pending_messages = None

    def test_pre_heat_fires_when_idle(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._reset_preheat_state()
        try:
            llm = self._make_llm()
            msgs = [{"role": "user", "content": "warm me"}]
            with patch.object(OllamaLLM, '_fire_pre_heat') as mock_fire:
                llm.pre_heat_cache(msgs)
            assert OllamaLLM._pre_heat_in_flight is True
            mock_fire.assert_called_once_with(msgs)
            assert OllamaLLM._pre_heat_pending_messages is None
        finally:
            self._reset_preheat_state()

    def test_pre_heat_defers_when_in_flight(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._reset_preheat_state()
        try:
            llm = self._make_llm()
            OllamaLLM._pre_heat_in_flight = True
            msgs = [{"role": "user", "content": "latest"}]
            with patch.object(OllamaLLM, '_fire_pre_heat') as mock_fire:
                llm.pre_heat_cache(msgs)
            mock_fire.assert_not_called()
            assert OllamaLLM._pre_heat_pending_messages == msgs
        finally:
            self._reset_preheat_state()

    def test_pre_heat_appends_stub_user_message_to_system_only_prompt(self):
        # Ollama 500s ("no user query found in messages") on a system-only
        # request, which is exactly what the prompt-assembled callback sends.
        import time
        self._reset_preheat_state()
        try:
            llm = self._make_llm()
            captured = {}
            def fake_stream(messages, num_predict=None):
                captured['messages'] = messages
                return iter(())
            with patch.object(llm, 'create_streaming_completion', side_effect=fake_stream), \
                 patch.object(llm, '_pre_heat_raw_truncated', return_value=False):
                llm.pre_heat_cache([{"role": "system", "content": "sys"}])
                for _ in range(100):
                    if 'messages' in captured:
                        break
                    time.sleep(0.01)
            assert captured['messages'][0] == {"role": "system", "content": "sys"}
            assert captured['messages'][-1]["role"] == "user"
        finally:
            self._reset_preheat_state()

    def test_pre_heat_leaves_prompt_with_user_message_untouched(self):
        import time
        self._reset_preheat_state()
        try:
            llm = self._make_llm()
            msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
            captured = {}
            def fake_stream(messages, num_predict=None):
                captured['messages'] = messages
                return iter(())
            with patch.object(llm, 'create_streaming_completion', side_effect=fake_stream), \
                 patch.object(llm, '_pre_heat_raw_truncated', return_value=False):
                llm.pre_heat_cache(msgs)
                for _ in range(100):
                    if 'messages' in captured:
                        break
                    time.sleep(0.01)
            assert captured['messages'] == msgs
        finally:
            self._reset_preheat_state()

    # --- raw truncated pre-heat -----------------------------------------
    # The MLX runner snapshots the KV cache at end-of-prompt minus 4 tokens.
    # A chat pre-heat ends with the template's generation suffix, placing that
    # snapshot past where the next real request diverges (unusable). The raw
    # path cuts the suffix so the snapshot lands inside the shared prefix.

    _RENDERED = ("<|im_start|>system\nsys<|im_end|>\n"
                 "<|im_start|>user\nnote content<|im_end|>\n"
                 "<|im_start|>assistant\n<think>\n\n</think>\n\n")

    def test_raw_pre_heat_truncates_generation_suffix(self):
        llm = self._make_llm()
        with patch.object(llm, '_rendered_prompt', return_value=self._RENDERED), \
             patch('assistant.llm.OllamaLLM.requests.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            assert llm._pre_heat_raw_truncated([{"role": "user", "content": "note content"}]) is True
        assert mock_post.call_count == 1
        url = mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        assert url.endswith("/api/generate")
        assert body["raw"] is True
        assert body["options"]["num_predict"] == 1
        assert body["prompt"].endswith("note content"), "prompt must be cut at the end of the last turn's content"
        assert "<|im_start|>assistant" not in body["prompt"]

    def test_raw_pre_heat_falls_back_when_render_unavailable(self):
        llm = self._make_llm()
        with patch.object(llm, '_rendered_prompt', return_value=None), \
             patch('assistant.llm.OllamaLLM.requests.post') as mock_post:
            assert llm._pre_heat_raw_truncated([{"role": "user", "content": "x"}]) is False
        mock_post.assert_not_called()

    def test_raw_pre_heat_falls_back_when_suffix_marker_missing(self):
        llm = self._make_llm()
        with patch.object(llm, '_rendered_prompt', return_value="some other template format"), \
             patch('assistant.llm.OllamaLLM.requests.post') as mock_post:
            assert llm._pre_heat_raw_truncated([{"role": "user", "content": "x"}]) is False
        mock_post.assert_not_called()

    def test_pre_heat_keeps_only_latest_pending(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._reset_preheat_state()
        try:
            llm = self._make_llm()
            OllamaLLM._pre_heat_in_flight = True
            with patch.object(OllamaLLM, '_fire_pre_heat'):
                llm.pre_heat_cache([{"role": "user", "content": "first"}])
                llm.pre_heat_cache([{"role": "user", "content": "second"}])
            assert OllamaLLM._pre_heat_pending_messages == [{"role": "user", "content": "second"}]
        finally:
            self._reset_preheat_state()


# ===========================================================================
# TestOllamaRemoteLog
# ===========================================================================

def _ssh_result(returncode=0, stdout='', stderr=''):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class TestOllamaRemoteLog:
    """Remote-Ollama diagnostics: cache-metric log reads go over ssh when base_url
    points at another machine."""

    @patch('assistant.llm.OllamaLLM.config')
    def test_local_base_url_uses_local_log(self, mock_config):
        mock_config.get.side_effect = lambda key, default=None: default
        from assistant.llm.OllamaLLM import OllamaLLM
        OllamaLLM(base_url='http://localhost:11434')
        assert OllamaLLM._ssh_log is None

    @patch('assistant.llm.OllamaLLM.config')
    def test_remote_base_url_enables_ssh_log(self, mock_config):
        mock_config.get.side_effect = lambda key, default=None: default
        from assistant.llm.OllamaLLM import OllamaLLM
        try:
            OllamaLLM(base_url='http://100.64.0.10:11434')
            assert OllamaLLM._ssh_log is not None
            assert OllamaLLM._ssh_log.host == '100.64.0.10'
            assert OllamaLLM._ssh_log.path.endswith('/log/ollama.log')
        finally:
            OllamaLLM._ssh_log = None

    @patch('assistant.llm.OllamaLLM.config')
    def test_ssh_host_and_log_path_from_config(self, mock_config):
        overrides = {'ollama.ssh_host': 'user@studio', 'ollama.remote_log_path': '/srv/ollama.log'}
        mock_config.get.side_effect = lambda key, default=None: overrides.get(key, default)
        from assistant.llm.OllamaLLM import OllamaLLM
        try:
            OllamaLLM(base_url='http://studio:11434')
            assert OllamaLLM._ssh_log.host == 'user@studio'
            assert OllamaLLM._ssh_log.path == '/srv/ollama.log'
        finally:
            OllamaLLM._ssh_log = None

    @patch('assistant.llm.OllamaLLM.config')
    def test_local_base_url_clears_previous_ssh_log(self, mock_config):
        mock_config.get.side_effect = lambda key, default=None: default
        from assistant.llm.OllamaLLM import OllamaLLM
        try:
            OllamaLLM(base_url='http://studio:11434')
            assert OllamaLLM._ssh_log is not None
            OllamaLLM(base_url='http://localhost:11434')
            assert OllamaLLM._ssh_log is None
        finally:
            OllamaLLM._ssh_log = None

    @patch('assistant.llm.OllamaLLM.subprocess.run')
    def test_ssh_size_parses_wc_output(self, mock_run):
        from assistant.llm.OllamaLLM import _SshRemoteLog
        mock_run.return_value = _ssh_result(stdout='   1234\n')
        assert _SshRemoteLog('studio', '/srv/ollama.log').size() == 1234

    @patch('assistant.llm.OllamaLLM.subprocess.run')
    def test_ssh_size_zero_when_remote_file_missing(self, mock_run):
        from assistant.llm.OllamaLLM import _SshRemoteLog
        mock_run.return_value = _ssh_result(stdout='MISSING\n')
        assert _SshRemoteLog('studio', '/srv/ollama.log').size() == 0

    @patch('assistant.llm.OllamaLLM.subprocess.run')
    def test_ssh_read_from_tails_at_offset(self, mock_run):
        from assistant.llm.OllamaLLM import _SshRemoteLog
        line = 'msg="loading cache slot" id=0 cache=6533 prompt=6344 used=5786 remaining=558\n'
        mock_run.return_value = _ssh_result(stdout=line)
        assert _SshRemoteLog('studio', '/srv/ollama.log').read_from(100) == line
        remote_cmd = mock_run.call_args[0][0][-1]
        assert 'tail -c +101' in remote_cmd  # tail offsets are 1-based

    @patch('assistant.llm.OllamaLLM.subprocess.run')
    def test_ssh_failure_engages_cooldown(self, mock_run):
        # A dead ssh connection must not stall the request path: after one
        # failure, further calls return None without spawning ssh again.
        from assistant.llm.OllamaLLM import _SshRemoteLog
        mock_run.return_value = _ssh_result(returncode=255, stderr='Connection refused')
        log = _SshRemoteLog('studio', '/srv/ollama.log')
        assert log.size() is None
        assert log.read_from(0) is None
        mock_run.assert_called_once()

    @patch('assistant.llm.OllamaLLM.subprocess.run')
    def test_ssh_cooldown_expires_and_retries(self, mock_run):
        from assistant.llm.OllamaLLM import _SshRemoteLog
        mock_run.side_effect = [_ssh_result(returncode=255, stderr='down'),
                                _ssh_result(stdout='42\n')]
        log = _SshRemoteLog('studio', '/srv/ollama.log')
        assert log.size() is None
        log._disabled_until = 0.0  # simulate cooldown expiry
        assert log.size() == 42

    def test_read_cache_metrics_via_ssh_log(self):
        from assistant.llm.OllamaLLM import OllamaLLM, _SshRemoteLog
        line = 'msg="loading cache slot" id=0 cache=6533 prompt=6344 used=5786 remaining=558\n'
        original_anchor = OllamaLLM._prefill_log_start
        try:
            OllamaLLM._ssh_log = MagicMock(spec=_SshRemoteLog)
            OllamaLLM._ssh_log.read_from.return_value = line
            OllamaLLM._prefill_log_start = 500
            assert OllamaLLM._read_cache_metrics() == (5786, 558)
            OllamaLLM._ssh_log.read_from.assert_called_with(500)
        finally:
            OllamaLLM._ssh_log = None
            OllamaLLM._prefill_log_start = original_anchor

    def test_begin_prefill_disables_metrics_when_log_unreachable(self):
        # A None anchor (ssh down at request start) must disable cache metrics
        # for the request instead of scanning the log from byte 0.
        from assistant.llm.OllamaLLM import OllamaLLM
        original_anchor = OllamaLLM._prefill_log_start
        try:
            with patch.object(OllamaLLM, '_log_size', return_value=None):
                OllamaLLM._begin_prefill()
            assert OllamaLLM._prefill_log_start is None
            assert OllamaLLM._read_cache_metrics() is None
        finally:
            OllamaLLM._end_prefill()
            OllamaLLM._prefill_log_start = original_anchor


# ===========================================================================
# TestOllamaMlxLogFormat
# ===========================================================================

class TestOllamaMlxLogFormat:
    """The MLX-engine Ollama build logs a different format than the llama.cpp
    runner: 'cache hit/miss ... cached=C left=L' instead of 'loading cache slot
    ... used=N remaining=M', plus real incremental prefill progress lines."""

    def _with_log(self, tmp_path, content):
        log_file = tmp_path / "ollama.log"
        log_file.write_text(content)
        return log_file

    def _patched_state(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        return patch.multiple(OllamaLLM,
                              _ollama_log_path=str(tmp_path / "ollama.log"),
                              _prefill_log_start=0)

    def test_read_cache_metrics_parses_mlx_cache_hit(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._with_log(tmp_path,
                       'time=... level=INFO source=prefix_cache.go:124 '
                       'msg="cache hit" total=25572 matched=25553 cached=24575 left=997\n')
        with self._patched_state(tmp_path):
            assert OllamaLLM._read_cache_metrics() == (24575, 997)

    def test_read_cache_metrics_parses_mlx_cache_miss(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._with_log(tmp_path,
                       'time=... level=INFO source=prefix_cache.go:124 '
                       'msg="cache miss" total=17531 matched=2 cached=0 left=17531\n')
        with self._patched_state(tmp_path):
            assert OllamaLLM._read_cache_metrics() == (0, 17531)

    def test_read_cache_metrics_still_parses_llamacpp_format(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._with_log(tmp_path,
                       'msg="loading cache slot" id=0 cache=6533 prompt=6344 used=5786 remaining=558\n')
        with self._patched_state(tmp_path):
            assert OllamaLLM._read_cache_metrics() == (5786, 558)

    def test_read_prefill_progress_returns_last_line(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._with_log(tmp_path,
                       'msg="cache miss" total=17531 matched=2 cached=0 left=17531\n'
                       'msg="Prompt processing progress" processed=2048 total=17531\n'
                       'msg="Prompt processing progress" processed=4096 total=17531\n')
        with self._patched_state(tmp_path):
            assert OllamaLLM._read_prefill_progress() == (4096, 17531)

    def test_read_prefill_progress_none_without_progress_lines(self, tmp_path):
        from assistant.llm.OllamaLLM import OllamaLLM
        self._with_log(tmp_path,
                       'msg="loading cache slot" id=0 cache=1 prompt=1 used=1 remaining=1\n')
        with self._patched_state(tmp_path):
            assert OllamaLLM._read_prefill_progress() is None

    def test_prefill_progress_uses_mlx_progress_lines(self):
        from assistant.llm.OllamaLLM import OllamaLLM
        with patch.object(OllamaLLM, '_log_size', return_value=0):
            OllamaLLM._begin_prefill()
        try:
            with patch.object(OllamaLLM, '_read_cache_metrics', return_value=(0, 17531)), \
                 patch.object(OllamaLLM, '_read_prefill_progress', return_value=(8192, 17531)):
                progress = OllamaLLM.prefill_progress()
            assert progress == pytest.approx(8192 / 17531 * 100.0)
        finally:
            OllamaLLM._end_prefill()

    def test_prefill_progress_throttles_progress_log_reads(self):
        # Over ssh every progress read is a round trip; polls inside the
        # throttle window must serve the cached value without re-reading.
        from assistant.llm.OllamaLLM import OllamaLLM
        with patch.object(OllamaLLM, '_log_size', return_value=0):
            OllamaLLM._begin_prefill()
        try:
            with patch.object(OllamaLLM, '_read_cache_metrics', return_value=(0, 17531)), \
                 patch.object(OllamaLLM, '_read_prefill_progress',
                              return_value=(2048, 17531)) as mock_read:
                first = OllamaLLM.prefill_progress()
                second = OllamaLLM.prefill_progress()
            assert first == second == pytest.approx(2048 / 17531 * 100.0)
            mock_read.assert_called_once()
        finally:
            OllamaLLM._end_prefill()

    def test_prefill_progress_falls_back_to_time_estimate(self):
        # llama.cpp runner: no progress lines — the elapsed-time estimate applies.
        from assistant.llm.OllamaLLM import OllamaLLM
        with patch.object(OllamaLLM, '_log_size', return_value=0):
            OllamaLLM._begin_prefill()
        try:
            with patch.object(OllamaLLM, '_read_cache_metrics', return_value=(5786, 558)), \
                 patch.object(OllamaLLM, '_read_prefill_progress', return_value=None):
                progress = OllamaLLM.prefill_progress()
            assert 0.0 <= progress <= 99.0
        finally:
            OllamaLLM._end_prefill()


# ===========================================================================
# missing_api_key — the question a caller asks before initialize() when it would rather
# sit out than crash the boot (the cache auditor, on an install without a Gemini key).
# ===========================================================================

class TestMissingApiKey:
    @patch('assistant.llm.GeminiLLM.config')
    def test_gemini_names_its_key_only_when_unset(self, mock_config):
        from assistant.llm.GeminiLLM import GeminiLLM
        mock_config.get_api_key.return_value = ""
        assert GeminiLLM().missing_api_key() == "api_keys.gemini"
        mock_config.get_api_key.return_value = "g-key"
        assert GeminiLLM().missing_api_key() is None

    @patch('assistant.llm.ClaudeLLM.config')
    def test_claude_names_its_key_only_when_unset(self, mock_config):
        from assistant.llm.ClaudeLLM import ClaudeLLM
        mock_config.get_api_key.return_value = None
        assert ClaudeLLM().missing_api_key() == "api_keys.anthropic"
        mock_config.get_api_key.return_value = "a-key"
        assert ClaudeLLM().missing_api_key() is None

    @patch('assistant.llm.ChatGPTLLM.config')
    def test_chatgpt_family_names_the_key_it_was_built_for(self, mock_config):
        # Grok and the other OpenAI-compatible backends share the class; the answer must
        # point at *their* key, not at api_keys.openai.
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        mock_config.get_api_key.return_value = ""
        assert ChatGPTLLM(api_key_name='grok').missing_api_key() == "api_keys.grok"
        assert ChatGPTLLM().missing_api_key() == "api_keys.openai"
        mock_config.get_api_key.return_value = "o-key"
        assert ChatGPTLLM(api_key_name='grok').missing_api_key() is None

    def test_the_base_class_reports_nothing_missing(self):
        assert ConcreteTestLLM().missing_api_key() is None

    @patch('assistant.llm.ChatGPTLLM.config')
    @patch('assistant.llm.ClaudeLLM.config')
    @patch('assistant.llm.GeminiLLM.config')
    def test_a_key_stored_after_construction_reaches_the_same_instance(self, gemini_cfg, claude_cfg, gpt_cfg):
        """dfa4b591: the key is read at every use, not captured in __init__. The LLM objects
        are built once at boot; a key the user stores from the chat (config_request) must
        reach the next request without a restart."""
        from assistant.llm.GeminiLLM import GeminiLLM
        from assistant.llm.ClaudeLLM import ClaudeLLM
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        for cfg in (gemini_cfg, claude_cfg, gpt_cfg):
            cfg.get_api_key.return_value = None
        llms = [GeminiLLM(), ClaudeLLM(), ChatGPTLLM(api_key_name='grok')]
        assert [llm.missing_api_key() for llm in llms] == ["api_keys.gemini", "api_keys.anthropic", "api_keys.grok"]

        for cfg in (gemini_cfg, claude_cfg, gpt_cfg):
            cfg.get_api_key.return_value = "stored-later"
        assert [llm.missing_api_key() for llm in llms] == [None, None, None]
        assert [llm.api_key for llm in llms] == ["stored-later"] * 3

    def test_api_key_name_is_known_without_the_key(self):
        # An integration running on a tier declares the tier backend's key as the one it
        # needs, so the name must come from the class alone — set or not.
        from assistant.llm.GeminiLLM import GeminiLLM
        from assistant.llm.ClaudeLLM import ClaudeLLM
        from assistant.llm.ChatGPTLLM import ChatGPTLLM
        assert GeminiLLM().api_key_name() == "api_keys.gemini"
        assert ClaudeLLM().api_key_name() == "api_keys.anthropic"
        assert ChatGPTLLM(api_key_name='grok').api_key_name() == "api_keys.grok"
        assert ConcreteTestLLM().api_key_name() is None

    def test_tier_api_key_name_builds_the_tier_backend_uninitialized(self, monkeypatch):
        from assistant.llm import tier_api_key_name
        monkeypatch.setattr('assistant.llm.parse_llm_config', lambda tier: ('GeminiLLM', 'gemini-3.8-flash'))
        with patch('assistant.llm.GeminiLLM.GeminiLLM.initialize') as init:
            assert tier_api_key_name('photo_indexing') == "api_keys.gemini"
        init.assert_not_called()
        monkeypatch.setattr('assistant.llm.parse_llm_config', lambda tier: ('OllamaLLM', None))
        assert tier_api_key_name('photo_indexing') is None

    def test_the_wrappers_forward_the_backend_answer(self, monkeypatch):
        # create_llm() always hands out a VaultLLM, and the cache auditor wraps that in a
        # CachedLLM: a wrapper that fell back to the base class's None would send the
        # auditor into initialize() and crash the boot without the key.
        from assistant.llm.VaultLLM import VaultLLM
        from assistant.llm.CachedLLM import CachedLLM
        monkeypatch.setattr('common.FloxyDatabase.FloxyDatabase.get_instance', staticmethod(lambda: Mock()))
        backend = Mock(spec=AbstractLLM)
        backend.missing_api_key.return_value = "api_keys.gemini"
        assert VaultLLM(backend, Mock()).missing_api_key() == "api_keys.gemini"
        assert CachedLLM(VaultLLM(backend, Mock())).missing_api_key() == "api_keys.gemini"
        backend.missing_api_key.return_value = None
        assert CachedLLM(VaultLLM(backend, Mock())).missing_api_key() is None
        backend.api_key_name.return_value = "api_keys.gemini"
        assert CachedLLM(VaultLLM(backend, Mock())).api_key_name() == "api_keys.gemini"
