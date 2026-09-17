import time
from typing import Generator

import httpx

from .AbstractLLM import AbstractLLM, LLMRefusalError
from anthropic import Anthropic, APIConnectionError, APIStatusError
from common.ConfigManager import config
from common.DevelopmentConfig import CLAUDE_STATIC_CACHE_TTL
from common.logging import trace, info


class ClaudeLLM(AbstractLLM):
    """Talks to Anthropic's Messages API with prompt caching, extended thinking and fast mode.

    Places cache_control breakpoints from the prompt's CACHE_BREAKPOINT_MARKERs (3 system + 1
    history), retries retryable status errors and raises LLMRefusalError when the safety layer
    keeps refusing.
    """

    FAST_MODE_BETA = "fast-mode-2026-02-01"
    REFUSAL_FALLBACK_MODEL = "claude-opus-5"
    REFUSAL_FALLBACK_CACHE_TTL = "5m"

    # CACHE_BREAKPOINT_MARKER is inherited from AbstractLLM.
    # Anthropic allows 4 cache_control breakpoints per request; _build_api_params
    # reserves one for the message-history breakpoint, leaving 3 for the system
    # prompt. Markers beyond this limit are ignored (their text joins the tail).
    MAX_SYSTEM_CACHE_BLOCKS = 3

    # Output caps. With adaptive thinking the thinking tokens count against max_tokens too,
    # so a thinking call that must emit a long answer (the memory curator rewrites a ~8k-token
    # bank) needs real headroom: at 16000 the curator's replies were cut mid-sentence for weeks.
    THINKING_MAX_TOKENS = 32000
    MAX_TOKENS = 4096
    # The SDK refuses a non-streaming request whose max_tokens implies more than 10 minutes
    # (about 21k tokens) unless a timeout is given explicitly. 32000 tokens take ~7 minutes
    # at the rate the nightly curation has shown, so 20 minutes leaves headroom.
    NON_STREAMING_TIMEOUT = httpx.Timeout(20 * 60, connect=5.0)

    def __init__(self, model: str = "claude-sonnet-4-5", speed: str | None = None, prompt_cache: bool = True,
                 static_cache_ttl: str | None = None):
        self.model = model
        self.speed = speed
        self.prompt_cache = prompt_cache
        self.static_cache_ttl = static_cache_ttl or CLAUDE_STATIC_CACHE_TTL
        self.client = None
        self._last_stop_reason = None

    def name(self) -> str:
        suffix = " (fast)" if self.speed == "fast" else ""
        return f"Claude ({self.model}){suffix}"

    @property
    def api_key(self) -> str | None:
        """Read at every use so a key stored at runtime reaches the next request (see GeminiLLM)."""
        return config.get_api_key('anthropic')

    def api_key_name(self) -> str:
        return "api_keys.anthropic"

    def missing_api_key(self) -> str | None:
        return None if self.api_key else self.api_key_name()

    def provider(self) -> str:
        return "Claude"

    def display_model(self) -> str:
        """"Fable" from claude-fable-5-1, "Opus" from claude-opus-5 — the family, spoken."""
        parts = self.model.split("-")
        return parts[1].capitalize() if len(parts) > 1 else self.model

    def refusal_fallback(self) -> AbstractLLM | None:
        """Fable's dual-use classifier refuses ordinary home-automation turns (e.g. any
        request right after a "server's just come up, nothing configured" reply) and does
        so deterministically, so re-sending never helps. Opus answers the same request;
        hand it over for this one turn. Refusals come in bursts — the turns following a
        refused one tend to be refused too — so the fallback caches its prompt like the
        primary does, but with the 5-minute TTL: the first fallback of a burst pays the
        1.25x write instead of the 1h tier's 2x, every later one within the burst reads
        at 0.1x, and a lone refusal wastes only a quarter of its input. The prompt is
        built for the primary before the fallback is chosen, so its cached prefix is the
        same bytes on every fallback. Opus 5 does have a fast mode, but at twice the
        price; a refused turn is already being paid for twice, so the speed flag is not
        carried over."""
        if not self.model.startswith("claude-fable"):
            return None
        return ClaudeLLM(self.REFUSAL_FALLBACK_MODEL, static_cache_ttl=self.REFUSAL_FALLBACK_CACHE_TTL)

    def supports_native_thinking(self) -> bool:
        """Claude supports native thinking via extended thinking API."""
        return True

    def prefers_prepended_user_query(self) -> bool:
        """Prepending the user query to the top of the system prompt puts per-request
        content at byte 0 of the cached prefix, defeating Anthropic prompt caching
        (every request becomes a cache write, 0 reads). It nudges accuracy up, so it
        stays on for older Claude models — but on Fable the cache economics are the
        steepest (1h writes at 2x, reads at 0.1x) and prompt scaffolding helps least,
        so Fable opts out and lets the shared prefix cache work."""
        return not self.model.startswith("claude-fable")

    def set_prompt_cache(self, enabled: bool):
        """Toggle Anthropic prompt caching for the next request. Prepending the user
        query (see prefers_prepended_user_query) puts per-request content at byte 0 of
        the cached prefix, so caching there only ever writes and never reads — pure
        waste. RequestProcessor re-sets this flag on every dialog request based on
        whether it prepends. Note this is sticky instance state: any other consumer
        reusing this instance inherits the last value set (background agents are
        unaffected — they create their own instances, which default to caching on)."""
        self.prompt_cache = enabled

    def initialize(self):
        """Initialize the Anthropic client"""
        self.client = Anthropic(api_key=self.api_key)

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI message format to Anthropic format.

        Returns:
            Tuple of (system_message, anthropic_messages)
        """
        system_message = None
        anthropic_messages = []

        for msg in messages:
            if msg['role'] == 'system':
                system_message = msg['content']
            elif msg['role'] in ('user', 'assistant'):
                anthropic_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })

        return system_message, anthropic_messages

    def _build_cached_system(self, system_message: str) -> list[dict]:
        """Build the `system` field as Anthropic content blocks with cache breakpoints.

        The system message may contain up to MAX_SYSTEM_CACHE_BLOCKS
        CACHE_BREAKPOINT_MARKERs, ordered stable → volatile. Each marker ends a
        cached block at self.static_cache_ttl (default 1h). A breakpoint caches
        the cumulative prefix up to itself, so when a segment's bytes change only
        that segment's block and the ones after it re-write — earlier breakpoints
        still read. Text after the last marker is the uncached volatile tail
        (e.g. per-request instructions, time). Without any marker, fall back to a
        single 5-minute cached block of the whole system message.
        """
        if self.CACHE_BREAKPOINT_MARKER not in system_message:
            return [{"type": "text", "text": system_message, "cache_control": {"type": "ephemeral"}}]

        segments = system_message.split(self.CACHE_BREAKPOINT_MARKER)
        cached_segments, tail_segments = segments[:-1], [segments[-1]]
        if len(cached_segments) > self.MAX_SYSTEM_CACHE_BLOCKS:
            # Keep the FIRST markers — the prompt orders content stable → volatile,
            # so early breakpoints guard the segments most likely to stay byte-identical.
            info(f"System prompt has {len(cached_segments)} cache breakpoints, "
                 f"only the first {self.MAX_SYSTEM_CACHE_BLOCKS} are used — "
                 f"the remaining segments join the uncached tail")
            tail_segments = cached_segments[self.MAX_SYSTEM_CACHE_BLOCKS:] + tail_segments
            cached_segments = cached_segments[:self.MAX_SYSTEM_CACHE_BLOCKS]

        blocks = []
        for segment in cached_segments:
            text = segment.strip()
            if not text:
                continue  # adjacent markers — a breakpoint already sits here
            blocks.append({
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral", "ttl": self.static_cache_ttl},
            })
        tail = "\n\n".join(s.strip() for s in tail_segments if s.strip())
        if tail:
            blocks.append({"type": "text", "text": tail})
        return blocks

    @staticmethod
    def _with_cache_breakpoint(message: dict) -> dict | None:
        """Return a copy of `message` with a cache breakpoint on its last content block.

        Returns None when the breakpoint would land on an empty text block: the
        Anthropic API rejects cache_control on empty text ("cache_control cannot be
        set for empty text blocks"), and a blank turn (e.g. an interrupted reply
        that got stored empty) must not crash the whole request. Skipping the
        breakpoint just forgoes caching for that one turn.
        """
        content = message['content']
        if isinstance(content, list):
            if not content:
                return None
            blocks = [block.copy() if isinstance(block, dict) else block for block in content]
            last = blocks[-1]
            if isinstance(last, dict) and last.get("type", "text") == "text" and not (last.get("text") or "").strip():
                return None
            blocks[-1] = {**last, "cache_control": {"type": "ephemeral"}}
            return {'role': message['role'], 'content': blocks}
        if not content.strip():
            return None
        return {
            'role': message['role'],
            'content': [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}],
        }

    def _build_api_params(self, messages: list[dict], thinking: bool, stream: bool) -> dict:
        """Build common API call parameters."""
        system_message, anthropic_messages = self._convert_messages(messages)

        if system_message and self.prompt_cache:
            system = self._build_cached_system(system_message)
        elif system_message:
            system = system_message.replace(self.CACHE_BREAKPOINT_MARKER, "")
        else:
            system = None

        if self.prompt_cache and len(anthropic_messages) >= 2:
            cached = self._with_cache_breakpoint(anthropic_messages[-2])
            if cached is not None:
                anthropic_messages[-2] = cached

        api_params = {
            'model': self.model,
            'messages': anthropic_messages,
            'max_tokens': self.THINKING_MAX_TOKENS if thinking else self.MAX_TOKENS,
            'stream': stream
        }
        if not stream:
            api_params['timeout'] = self.NON_STREAMING_TIMEOUT

        if system:
            api_params['system'] = system

        if thinking:
            api_params['thinking'] = {"type": "adaptive"}

        return api_params

    @staticmethod
    def _cache_creation_breakdown(usage, cache_created: int) -> tuple[int, int]:
        """Split cache-creation tokens into (5-minute, 1-hour) writes.

        Anthropic reports the per-TTL split under usage.cache_creation. When that
        detail is absent (older API shape, or a mock without it), attribute all
        created tokens to the cheaper 5-minute rate so the two always sum to the
        reported total.
        """
        detail = getattr(usage, 'cache_creation', None)
        if detail is not None:
            c5m = getattr(detail, 'ephemeral_5m_input_tokens', None)
            c1h = getattr(detail, 'ephemeral_1h_input_tokens', None)
            if isinstance(c5m, int) or isinstance(c1h, int):
                return (c5m if isinstance(c5m, int) else 0), (c1h if isinstance(c1h, int) else 0)
        return cache_created, 0

    def _log_cache_usage(self, usage):
        cache_created = getattr(usage, 'cache_creation_input_tokens', 0) or 0
        cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
        input_tokens = getattr(usage, 'input_tokens', 0) or 0
        created_5m, created_1h = self._cache_creation_breakdown(usage, cache_created)
        total = input_tokens + cache_created + cache_read
        if total > 0:
            pct = round(cache_read / total * 100)
            trace(f"Claude cache: {cache_read} read, {cache_created} created "
                  f"({created_5m} 5m + {created_1h} 1h), {input_tokens} uncached "
                  f"(total {total}, {pct}% cached)")
            from common.TTFTMonitor import TTFTMonitor
            TTFTMonitor.get_instance().record_cache_pct(pct)

    def _log_output_tokens(self, usage):
        raw_output = getattr(usage, 'output_tokens', None)
        output_tokens = raw_output if isinstance(raw_output, int) else 0
        if output_tokens > 0:
            trace(f"Claude output: {output_tokens} tokens")

    def _create_message(self, api_params: dict):
        if self.speed == "fast":
            return self.client.beta.messages.create(
                **api_params, speed="fast", betas=[self.FAST_MODE_BETA]
            )
        return self.client.messages.create(**api_params)

    def _report_ttft(self, ttft: float):
        from common.TTFTMonitor import TTFTMonitor
        TTFTMonitor.get_instance().record(self.model, round(ttft, 2))

    OVERLOADED_RETRY_DELAYS = [0.5, 1.5, 3, 3, 3]

    # Fable (claude-fable-5) occasionally completes a turn with only 2-6 output tokens
    # and no text block at all — observed repeatedly in prod (e.g. 2026-06-11 10:29).
    # Nothing was streamed downstream in those cases, so re-issuing the identical
    # request is safe and has so far always produced a normal reply.
    EMPTY_REPLY_RETRIES = 2

    @staticmethod
    def _is_retryable(e: APIStatusError) -> bool:
        if e.status_code in (500, 529):
            return True
        return (hasattr(e, 'body') and isinstance(e.body, dict)
                and isinstance(e.body.get('error'), dict)
                and e.body['error'].get('type') == 'overloaded_error')

    def _stream_once(self, api_params: dict) -> Generator[str, None, None]:
        """One streaming request: yield text deltas, log cache/output usage.

        When the stream completes without a single text delta (the Fable
        empty-reply misfire), log stop_reason and the block types we did get so
        the failure mode is diagnosable from floxy.out.
        """
        t_start = time.time()
        first_token_seen = False
        stop_reason = None
        block_types = []
        self._last_stop_reason = None
        stream = self._create_message(api_params)
        for event in stream:
            if event.type == 'message_start' and hasattr(event.message, 'usage'):
                self._log_cache_usage(event.message.usage)
            elif event.type == 'content_block_start':
                block_types.append(getattr(event.content_block, 'type', '?'))
            elif event.type == 'content_block_delta' and event.delta.type == 'text_delta':
                if not first_token_seen:
                    first_token_seen = True
                    self._report_ttft(time.time() - t_start)
                yield event.delta.text
            elif event.type == 'message_delta':
                stop_reason = getattr(event.delta, 'stop_reason', None) or stop_reason
                if hasattr(event, 'usage'):
                    self._log_output_tokens(event.usage)
        self._last_stop_reason = stop_reason
        if not first_token_seen:
            info(f"Claude stream ended with no text (stop_reason={stop_reason}, blocks={block_types or 'none'})")

    def _stream_with_status_retry(self, api_params: dict) -> Generator[str, None, None]:
        """Stream with retries on retryable API errors (500/529/overloaded) and on
        transport drops (long-lived SSE streams occasionally get cut by upstream edges).

        Transport drops are only retried while nothing has been yielded downstream —
        after that a re-issued request would duplicate the stream, so the error
        propagates for DialogManager to handle (it knows whether the yielded deltas
        were user-facing or just thinking text)."""
        max_attempts = len(self.OVERLOADED_RETRY_DELAYS) + 1
        for attempt, delay in enumerate(self.OVERLOADED_RETRY_DELAYS, start=1):
            yielded = 0
            try:
                for chunk in self._stream_once(api_params):
                    yielded += 1
                    yield chunk
                return
            except APIStatusError as e:
                if not self._is_retryable(e):
                    raise
                info(f"Claude API error (attempt {attempt}/{max_attempts}, status={e.status_code}), retrying in {delay}s...")
                time.sleep(delay)
            except (httpx.TransportError, APIConnectionError) as e:
                if yielded:
                    raise
                info(f"Claude stream dropped before any output ({type(e).__name__}, "
                     f"attempt {attempt}/{max_attempts}), retrying in {delay}s...")
                time.sleep(delay)
        # Final attempt — let exceptions propagate
        yield from self._stream_once(api_params)

    def create_streaming_completion(self, messages: list[dict], thinking: bool = False) -> Generator[str, None, None]:
        self._ensure_initialized()

        api_params = self._build_api_params(messages, thinking, stream=True)
        for attempt in range(self.EMPTY_REPLY_RETRIES + 1):
            produced_text = False
            for chunk in self._stream_with_status_retry(api_params):
                if chunk.strip():
                    produced_text = True
                yield chunk
            if produced_text:
                return
            # The safety classifier is deterministic on identical input: re-sending only
            # adds another refused request to the account, so the first refusal is final.
            if self._last_stop_reason == 'refusal':
                info("Claude refused the request (stop_reason=refusal) - not retrying, raising LLMRefusalError.")
                raise LLMRefusalError(f"{self.model} refused the request (stop_reason=refusal)")
            if attempt < self.EMPTY_REPLY_RETRIES:
                info(f"Claude replied with empty message (attempt {attempt + 1}/{self.EMPTY_REPLY_RETRIES + 1}), "
                     "re-sending the request...")
        info("Claude replied with empty message on every retry - passing the empty reply through.")

    def _complete_once(self, api_params: dict) -> str:
        max_attempts = len(self.OVERLOADED_RETRY_DELAYS) + 1
        response = None
        for attempt, delay in enumerate(self.OVERLOADED_RETRY_DELAYS, start=1):
            try:
                t_start = time.time()
                response = self._create_message(api_params)
                self._report_ttft(time.time() - t_start)
                break
            except APIStatusError as e:
                if not self._is_retryable(e):
                    raise
                info(f"Claude API error (attempt {attempt}/{max_attempts}, status={e.status_code}), retrying in {delay}s...")
                time.sleep(delay)
            except (httpx.TransportError, APIConnectionError) as e:
                info(f"Claude request failed on transport ({type(e).__name__}, "
                     f"attempt {attempt}/{max_attempts}), retrying in {delay}s...")
                time.sleep(delay)
        if response is None:
            t_start = time.time()
            response = self._create_message(api_params)
            self._report_ttft(time.time() - t_start)
        self._log_cache_usage(response.usage)
        self._log_output_tokens(response.usage)
        self._last_stop_reason = getattr(response, 'stop_reason', None)
        if self._last_stop_reason == 'max_tokens':
            info(f"Claude hit max_tokens={api_params['max_tokens']} - the reply is cut short")

        text = ''.join(block.text for block in response.content if block.type == 'text')
        if not text.strip():
            block_types = [getattr(block, 'type', '?') for block in response.content]
            info(f"Claude returned no text (stop_reason={getattr(response, 'stop_reason', None)}, "
                 f"blocks={block_types or 'none'})")
        return text

    def create_completion(self, messages: list[dict], thinking: bool = False) -> str:
        self._ensure_initialized()

        api_params = self._build_api_params(messages, thinking, stream=False)
        text = ""
        for attempt in range(self.EMPTY_REPLY_RETRIES + 1):
            text = self._complete_once(api_params)
            if text.strip():
                return text
            # Same rule as the streaming path: the classifier is deterministic, so the
            # first refusal is final — re-sending only adds refused requests.
            if self._last_stop_reason == 'refusal':
                info("Claude refused the request (stop_reason=refusal) - not retrying, raising LLMRefusalError.")
                raise LLMRefusalError(f"{self.model} refused the request (stop_reason=refusal)")
            if attempt < self.EMPTY_REPLY_RETRIES:
                info(f"Claude replied with empty message (attempt {attempt + 1}/{self.EMPTY_REPLY_RETRIES + 1}), "
                     f"re-sending the request...")
        info("Claude replied with empty message on every retry - returning the empty reply.")
        return text

    def create_completion_with_images(self, messages: list[dict], images: list) -> str:
        self._ensure_initialized()

        system_message, anthropic_messages = self._convert_messages(messages)

        content_blocks = []
        for img in images:
            if isinstance(img, (bytes, bytearray)):
                import base64
                data = base64.b64encode(img).decode('utf-8')
                media_type = "image/png" if img[:4] == b'\x89PNG' else "image/jpeg"
            else:
                data = img
                media_type = "image/jpeg"
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data}
            })

        if anthropic_messages and anthropic_messages[-1]['role'] == 'user':
            last_text = anthropic_messages[-1]['content']
            content_blocks.append({"type": "text", "text": last_text})
            anthropic_messages[-1] = {'role': 'user', 'content': content_blocks}

        api_params = {
            'model': self.model,
            'messages': anthropic_messages,
            'max_tokens': 4096,
        }
        if system_message:
            # This path skips _build_cached_system (no caching for one-shot image
            # calls), so the breakpoint markers must be stripped explicitly.
            api_params['system'] = self.strip_cache_markers(system_message)

        response = self.client.messages.create(**api_params)
        text = ''.join(block.text for block in response.content if block.type == 'text')
        if not text.strip():
            block_types = [getattr(block, 'type', '?') for block in response.content]
            info(f"Claude (image call) returned no text (stop_reason={getattr(response, 'stop_reason', None)}, "
                 f"blocks={block_types or 'none'})")
        return text
