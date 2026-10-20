import time
from typing import Generator

from .ChatGPTLLM import ChatGPTLLM
from common.ConfigManager import config
from common.logging import info, trace, fine


class Ds4LLM(ChatGPTLLM):
    """Talks to a local ds4-server (antirez's DwarfStar engine) through its OpenAI-compatible API.

    ds4 serves one large open-weight model (DeepSeek V4.1 Flash, GLM 5.3, Qwen3.8 Flash Next) resident
    on this machine's GPU, at `ds4.base_url` (default http://127.0.0.1:8000/v1); `model` is the id the
    server loaded (`/v1/models`). The server takes no API key, so the client sends a placeholder and
    this backend is keyless for the setup code (`api_key_name()` is None, like Ollama).

    The model reasons server-side: the stream carries `reasoning_content` deltas, yielded here inside
    <thinking> tags so the command processor swallows and times them like a prompted thinking block.
    How much it reasons is `reasoning_effort`: a request with thinking=True (the memory curator) gets
    "high", every other one the `ds4.reasoning_effort` key, default "low" so a conversation turn
    answers in seconds; "none" turns server-side reasoning off, and then the prompt's own
    <thinking> instruction applies instead.
    """

    DEFAULT_MODEL = 'deepseek-v4.1-flash'
    DEFAULT_BASE_URL = 'http://127.0.0.1:8000/v1'
    DEFAULT_REASONING_EFFORT = 'low'
    THINKING_REASONING_EFFORT = 'high'
    # ds4-server's spellings; "minimal" is the smallest non-zero level, "none" alone disables reasoning.
    REASONING_EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
    _logged_configs: set[tuple[str, str]] = set()

    def __init__(self, model: str | None = None, base_url: str | None = None):
        """`model` is the tier's `model:` when it has one, else `ds4.model`, else the default."""
        super().__init__(model=model or config.get('ds4.model', self.DEFAULT_MODEL), api_key_name='ds4',
                         base_url=base_url or config.get('ds4.base_url', self.DEFAULT_BASE_URL))
        if (self.model, self.base_url) not in Ds4LLM._logged_configs:
            Ds4LLM._logged_configs.add((self.model, self.base_url))
            info(f"Ds4LLM '{self.model}' using {self.base_url}, reasoning effort {self.reasoning_effort()}")

    @property
    def api_key(self) -> str:
        """ds4-server ignores the key, but the OpenAI client refuses to start without one."""
        return 'ds4'

    def api_key_name(self) -> None:
        return None

    def missing_api_key(self) -> None:
        return None

    def name(self) -> str:
        return "DS4"

    def display_model(self) -> str:
        return self.model

    def is_local(self) -> bool:
        return True

    def prefers_prepended_user_query(self) -> bool:
        """ds4-server keeps one live KV context and reuses it only for a prompt that extends the
        previous one; a query prepended to the system prompt changes byte 0 every turn, so every
        turn re-prefilled the whole prompt (`common=2` in ds4-server.log, 2026-10-20)."""
        return False

    @classmethod
    def reasoning_effort(cls) -> str:
        """The effort of an ordinary request (thinking=False): `ds4.reasoning_effort`, or the
        default; a spelling ds4 does not know falls back to the default and is logged once."""
        effort = str(config.get('ds4.reasoning_effort', cls.DEFAULT_REASONING_EFFORT)).lower()
        if effort not in cls.REASONING_EFFORTS:
            info(f"ds4.reasoning_effort '{effort}' is not one of {', '.join(cls.REASONING_EFFORTS)} — "
                 f"using {cls.DEFAULT_REASONING_EFFORT}")
            return cls.DEFAULT_REASONING_EFFORT
        return effort

    def supports_native_thinking(self) -> bool:
        return True

    def handles_thinking_internally(self) -> bool:
        """The model reasons on its own unless the effort is "none"."""
        return self.reasoning_effort() != 'none'

    def _effort_for(self, thinking: bool) -> str:
        return self.THINKING_REASONING_EFFORT if thinking else self.reasoning_effort()

    def _request_kwargs(self, messages: list[dict], thinking: bool, stream: bool) -> dict:
        kwargs = dict(model=self.model, messages=self._convert_anthropic_content(messages), stream=stream,
                      extra_body={'reasoning_effort': self._effort_for(thinking)})
        if stream:
            kwargs['stream_options'] = {'include_usage': True}
        return kwargs

    @staticmethod
    def _log_usage(usage, started: float, reasoning_chars: int = 0):
        """One line per request: prompt tokens with the server's cache hits, output tokens, time."""
        if usage is None:
            trace("ds4: response carried no usage")
            return
        details = getattr(usage, 'prompt_tokens_details', None)
        cached = getattr(details, 'cached_tokens', None) if details is not None else None
        cached_text = f", {cached} cached" if cached is not None else ""
        thought = f", {reasoning_chars} reasoning chars" if reasoning_chars else ""
        info(f"ds4 metrics: ↑ {usage.prompt_tokens} tokens{cached_text} | ↓ {usage.completion_tokens} tokens{thought} "
             f"| {time.monotonic() - started:.2f}s")

    def create_streaming_completion(self, messages: list[dict], thinking: bool = False,
                                    volatile_tail: int = 0) -> Generator[str, None, None]:
        """Reasoning deltas (`reasoning_content`) are yielded wrapped in <thinking> tags, the reply
        text after them; the stream's closing usage chunk is logged."""
        self._ensure_initialized()
        started = time.monotonic()
        response = self.client.chat.completions.create(**self._request_kwargs(messages, thinking, stream=True))
        in_thinking, reasoning_chars, usage = False, 0, None
        for chunk in response:
            if getattr(chunk, 'usage', None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            thought = getattr(delta, 'reasoning_content', None)
            if thought:
                if not in_thinking:
                    in_thinking = True
                    yield "<thinking>"
                reasoning_chars += len(thought)
                yield thought
            if delta.content:
                if in_thinking:
                    in_thinking = False
                    yield "</thinking>"
                yield delta.content
        if in_thinking:
            trace("ds4 stream ended inside the thinking block — closing it")
            yield "</thinking>"
        self._log_usage(usage, started, reasoning_chars)

    def create_completion(self, messages: list[dict], thinking: bool = False, volatile_tail: int = 0) -> str:
        """The reply text; the model's reasoning is logged, not returned."""
        self._ensure_initialized()
        started = time.monotonic()
        response = self.client.chat.completions.create(**self._request_kwargs(messages, thinking, stream=False))
        message = response.choices[0].message
        reasoning = getattr(message, 'reasoning_content', None) or ""
        if reasoning:
            fine(f"ds4 thought:\n{reasoning.strip()}")
        self._log_usage(getattr(response, 'usage', None), started, len(reasoning))
        return message.content or ""
