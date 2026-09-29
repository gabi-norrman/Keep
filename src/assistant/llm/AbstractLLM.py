import time
import traceback
from abc import ABC, abstractmethod
from typing import Generator

from common.logging import error, trace


class ThinkingNotSupportedError(Exception):
    """Raised when thinking mode is requested but not supported by this LLM."""
    pass


class LLMRefusalError(Exception):
    """Raised when the model's safety layer refused to answer (e.g. Anthropic
    stop_reason=refusal) and retries were exhausted."""
    pass


class LLMPreempted(Exception):
    """A background request (photo indexing) was aborted because an interactive request
    arrived for the same single-slot backend. Not a failure: the caller resends it once the
    backend is idle again."""
    pass


def api_error_message(e: Exception) -> str | None:
    """The server's own explanation of a failed API call, for showing to the user —
    "Your credit balance is too low…", "Overloaded" — or None when the failure never
    reached a server (transport drop, timeout, a bug of ours).

    Duck-typed so the SDKs need not be imported here: the Anthropic and OpenAI status
    errors carry `status_code` and the decoded `body` (`{"error": {"message": …}}`), the
    Gemini one carries `code` and `message`."""
    status = getattr(e, "status_code", None) or getattr(e, "code", None)
    if not isinstance(status, int):
        return None
    body = getattr(e, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    message = getattr(e, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return f"HTTP {status}"


class AbstractLLM(ABC):
    """Defines the contract every LLM backend implements, plus shared retry and cache-marker helpers.

    CACHE_BREAKPOINT_MARKER is embedded in res/prompt.md; ClaudeLLM turns it into Anthropic
    cache_control breakpoints while every other backend strips it via strip_cache_markers().
    """

    # Prompt-cache breakpoint marker embedded in res/prompt.md. ClaudeLLM consumes
    # these to place Anthropic cache_control breakpoints; every other backend must
    # strip them (see strip_cache_markers) so they don't reach the model as text.
    CACHE_BREAKPOINT_MARKER = "<<<CACHE_BREAKPOINT>>>"

    @classmethod
    def strip_cache_markers(cls, text):
        """Remove cache breakpoint markers from prompt text. No-op for non-str
        content (e.g. content-block lists, which never carry markers)."""
        if isinstance(text, str):
            return text.replace(cls.CACHE_BREAKPOINT_MARKER, "")
        return text

    def _ensure_initialized(self):
        """Verify the client has been initialized."""
        if not self.client:
            raise RuntimeError("Client not initialized. Call initialize() first.")

    def _retry(self, fn, max_retries=3):
        """Retry a callable up to max_retries times, with 1s sleep between attempts."""
        for attempt in range(max_retries):
            try:
                return fn()
            except (LLMRefusalError, LLMPreempted):
                raise  # deterministic / the caller resends when the backend is idle — no retry here
            except Exception as e:
                if attempt == max_retries - 1:
                    error(f"Failed after {max_retries} attempts")
                    trace(traceback.format_exc())
                    raise
                else:
                    trace(f"Failed to contact AI: {e}, attempt  {attempt + 1}.")
                    time.sleep(1)
        return "Could not get response from AI"

    def prefers_prepended_user_query(self) -> bool:
        """Returns True if this LLM prefers the user query prepended to the system prompt. Override in subclasses."""
        return True

    def set_prompt_cache(self, enabled: bool):
        """Enable/disable prompt caching for subsequent requests. Default: no-op.
        Override in subclasses that pay for caching (e.g. ClaudeLLM)."""
        pass

    def supports_native_thinking(self) -> bool:
        """Returns True if this LLM supports native thinking mode. Override in subclasses."""
        return False

    def handles_thinking_internally(self) -> bool:
        """True when the model reasons on its own, server-side, whether or not we ask: no
        prompt may then instruct it to write its reasoning into a <thinking> tag. Anthropic's
        classifier reads that instruction as reasoning extraction and refuses the whole
        request (stop_reason=refusal, category reasoning_extraction) — on Fable 5.1 and
        Opus 5 alike, so the fallback cannot help. Override in subclasses."""
        return False

    def prefers_byte_prefix_tail(self) -> bool:
        """True when the backend's cache restores only at the end of the previous request, so
        every request must be a byte prefix of the next one: the trailing turn is sent exactly
        as the history will render it and carries no per-request extras (see
        DevelopmentConfig.BYTE_PREFIX_TAIL). Override in subclasses."""
        return False

    def pre_heat_cache(self, messages: list[dict]):
        """Pre-heat the LLM's cache with the given messages. Default: no-op.
        Override in subclasses that benefit from cache warming (e.g., local LLMs)."""
        pass

    @property
    def last_stop_reason(self) -> str | None:
        """Why the most recent completion ended, in the provider's own words ('end_turn',
        'max_tokens', 'refusal'…), or None when the backend does not report it. A caller
        that persists a reply verbatim checks this for 'max_tokens' before trusting it."""
        return getattr(self, '_last_stop_reason', None)

    def think(self, system_message: str, user_message: str, images: list | None = None, thinking: bool = False,
              background: bool = False) -> str:
        """
        Sends a request to the LLM, blocks until done, and returns the response.
        `background` marks bulk work (photo indexing) that a single-slot backend runs only while
        idle and aborts (LLMPreempted) when a user request arrives; cloud backends ignore it.
        """
        def fn():
            if images:
                return self.create_completion_with_images(
                    [{'role': 'system', 'content': system_message}, {'role': 'user', 'content': user_message}],
                    images, background=background)
            else:
                return self.create_completion(
                    [{'role': 'system', 'content': system_message}, {'role': 'user', 'content': user_message}],
                    thinking=thinking)
        return self._retry(fn)

    def supports_video(self) -> bool:
        """True if this LLM accepts a video clip as input. Override in backends that do (Gemini)."""
        return False

    def max_video_bytes(self) -> int:
        """Largest clip think_with_video() can take; 0 when supports_video() is False."""
        return 0

    def think_with_video(self, system_message: str, user_message: str, video_bytes: bytes,
                         mime_type: str, fps: float | None = None) -> str:
        """Sends one video clip plus a prompt, blocks until done, and returns the response.
        Only meaningful when supports_video() is True."""
        raise NotImplementedError(f"{self.name()} cannot take video input")

    def think_with_messages(self, messages: list[dict], thinking: bool = False) -> str:
        """
        Sends a request to the LLM with a full message history, blocks until done, and returns the response.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            thinking: Enable native thinking mode if supported by this LLM
        """
        return self._retry(lambda: self.create_completion(messages, thinking=thinking))

    @abstractmethod
    def create_streaming_completion(self, messages: list[dict], thinking: bool = False) -> Generator[str, None, None]:
        """
        Create a streaming completion from the llm

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            thinking: Enable native thinking mode if supported by this LLM

        Yields:
            String chunks of the response
        """
        pass

    @abstractmethod
    def create_completion(self, messages: list[dict], thinking: bool = False) -> str:
        """
        Create a non-streaming completion from the llm

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            thinking: Enable native thinking mode if supported by this LLM

        Returns:
            String containing the complete response
        """
        pass

    @abstractmethod
    def create_completion_with_images(self, messages: list[dict], images: list, background: bool = False) -> str:
        """
        Create a non-streaming completion with images using OpenAI's API

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            images: List of images in one of these formats:
                - Base64 encoded strings (without data URI prefix)
                - Raw bytes
                - File paths (will be converted to base64)
            background: see think(); only a single-slot backend acts on it

        Returns:
            String containing the complete response
        """
        pass

    def is_local(self) -> bool:
        """True for a backend that serves one request at a time on our own hardware (Ollama):
        bulk callers then run one or two workers instead of a wide fan-out."""
        return False

    @abstractmethod
    def initialize(self):
        """Initialize the llm client"""
        pass

    @abstractmethod
    def name(self) -> str:
        """Return the name of this llm/AI"""
        pass

    def provider(self) -> str:
        """Who runs the server behind this LLM, as the user hears it in an error notice
        ("The Claude server returned an error: …"). name() unless the name carries a model."""
        return self.name()

    def display_model(self) -> str:
        """The model as the user hears it in a notice ("Fable", "Opus"). name() by default."""
        return self.name()

    def api_key_name(self) -> str | None:
        """The config key (api_keys.…) this backend reads, or None for a keyless backend
        (Ollama). Known from the class alone — an integration that runs on an LLM tier
        declares this key as the one it needs."""
        return None

    def missing_api_key(self) -> str | None:
        """The config key (api_keys.…) this backend needs but has not got, or None when it can
        run. A caller that would rather skip its job than fail on initialize() asks this first."""
        return None

    def refusal_fallback(self) -> "AbstractLLM | None":
        """Another LLM to answer a request this one's safety layer refused, or None when
        the refusal should reach the user as-is. Used once per request — the fallback's
        own refusal is final."""
        return None
