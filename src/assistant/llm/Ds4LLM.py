"""Talks to a local ds4-server through its OpenAI-compatible API and runs Keep's commands in DeepSeek's DSML call syntax.

ds4 (antirez's DwarfStar engine) serves one large open-weight model — DeepSeek V4.1 Flash by
default — resident on this machine's GPU, at `ds4.base_url` (default http://127.0.0.1:8000/v1);
`model` is the id the server loaded (`/v1/models`). The server takes no API key, so the client
sends a placeholder and this backend is keyless for the setup code (`api_key_name()` is None,
like Ollama).

The model reasons server-side: the stream carries `reasoning_content` deltas, yielded here
inside <thinking> tags so the command processor swallows and times them like a prompted
thinking block. How much it reasons is `reasoning_effort`: a request with thinking=True (the
memory curator) gets "high", every other one the `ds4.reasoning_effort` key, default "low" so
a conversation turn answers in seconds; "none" turns server-side reasoning off, and then the
prompt's own <thinking> instruction applies instead.

Commands: the model writes its calls as DSML blocks (`DsmlFormat`, the V4.1 spelling), and
ds4-server parses them itself whether or not the request declares tools — streamed, the raw
block arrives as content deltas and then one `tool_calls` delta carries the whole call. The
content is withheld from the first DSML sentinel on, and the merged `tool_calls` are written
back as call text in the active command format and yielded, so the processor sees the call
the model wrote in one piece (the server repairs a truncated block before it hands it over);
withheld text with no `tool_calls` behind it is released as written for Keep's own parser.

The live KV cache (step 5b of docs/plans/COMMAND_FORMATS_PLAN.md): every request declares one
static tool, so ds4 renders the history in its tool shape and the reasoning of every assistant
turn is replayed in it; on the way out `Ds4Replay` rebuilds Keep's text rows into that shape —
each past call as the `tool_calls` entry with the id ds4 assigned (ds4 then puts the sampled
tokens back itself), its result as a `role: tool` message — from a side store this class fills
after every reply. A follow-up turn then prefills only its own tail instead of the whole prompt.
"""
import json
import time
from typing import Generator

from .ChatGPTLLM import ChatGPTLLM
from .Ds4Replay import ReplayStore, replay
from .OllamaLLM import OllamaLLM
from assistant.commands.formats import DsmlFormat as dsml
from assistant.commands.formats.CommandFormats import CommandFormats
from common.ConfigManager import config
from common.logging import info, trace, fine, note


class Ds4LLM(ChatGPTLLM):
    """A local ds4-server as an LLM backend, with DSML as the command format."""

    DEFAULT_MODEL = 'deepseek-v4.1-flash'
    DEFAULT_BASE_URL = 'http://127.0.0.1:8000/v1'
    DEFAULT_REASONING_EFFORT = 'low'
    THINKING_REASONING_EFFORT = 'high'
    # ds4-server's spellings; "minimal" is the smallest non-zero level, "none" alone disables reasoning.
    REASONING_EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
    # ds4 parses a DSML block with or without tools declared, but its live KV cache keeps a
    # call turn only in the tool shape (reasoning replayed, the call as `tool_calls` by id), and
    # it renders the history in that shape once a request declares a tool — the same one every
    # time, so the prefix is stable. Ollama's static tool, for the same reason it has one.
    STATIC_TOOL = OllamaLLM.STATIC_TOOL
    _logged_configs: set[tuple[str, str]] = set()
    _replay_store: ReplayStore | None = None

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

    def default_command_format(self):
        """DeepSeek's DSML syntax: what the model family ds4 serves here was trained on."""
        return dsml.DsmlFormat

    def prefers_prepended_user_query(self) -> bool:
        """ds4-server keeps one live KV context and reuses it only for a prompt that extends the
        previous one; a query prepended to the system prompt changes byte 0 every turn, so every
        turn re-prefilled the whole prompt (`common=2` in ds4-server.log, 2026-10-20)."""
        return False

    @classmethod
    def reasoning_effort(cls) -> str:
        """The effort of an ordinary request (thinking=False): `ds4.reasoning_effort`, or the
        default; a spelling ds4 does not know falls back to the default and is logged."""
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

    @classmethod
    def replay_store(cls) -> ReplayStore:
        """The one side store every Ds4LLM instance fills and replays from (the tiers share
        the server and its one live context)."""
        if cls._replay_store is None:
            cls._replay_store = ReplayStore()
        return cls._replay_store

    def _request_kwargs(self, messages: list[dict], thinking: bool, stream: bool) -> dict:
        messages = replay(self._convert_anthropic_content(messages), self.replay_store())
        kwargs = dict(model=self.model, messages=messages, stream=stream, tools=[self.STATIC_TOOL],
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

    # --- the DSML side ----------------------------------------------------------------------

    @staticmethod
    def _rendered_calls(calls) -> list[tuple[str, object]]:
        """Parsed tool calls (the merged stream deltas, or a message's `tool_calls`) each
        written back as call text in the active command format — DSML under a ds4 smart tier,
        the smart tier's syntax otherwise, so the parser reads it — paired with the call; an
        entry without a name is dropped."""
        fmt = CommandFormats.active()
        rendered = []
        for call in calls:
            function = getattr(call, 'function', None)
            name = getattr(function, 'name', None)
            if not name:
                trace(f"ds4 tool call without a function name, dropped: {call!r}")
                continue
            arguments = getattr(function, 'arguments', None) or "{}"
            trace(f"ds4 parsed a {name} call — written back as {fmt.name} call text")
            rendered.append((fmt.render_tool_call(name, arguments), call))
        return rendered

    @staticmethod
    def _tail(rendered: list[tuple[str, object]]) -> str:
        return "\n".join(text for text, _ in rendered)

    def _remember(self, reply: str, reasoning: str, visible: str, rendered: list[tuple[str, object]]):
        """Give the replay store what the next request needs to send this reply back in the
        wire shape: `reply` as Keep's history will hold it, the reasoning, the content as ds4
        canonicalises it (trailing whitespace trimmed before a call block), each rendered call
        with ds4's id."""
        calls = [(text, getattr(call, 'id', None), call.function.name, getattr(call.function, 'arguments', None) or "{}")
                 for text, call in rendered]
        self.replay_store().remember_reply(reply, reasoning, visible.rstrip() if calls else visible, calls)

    @staticmethod
    def _split_at_sentinel(text: str) -> tuple[str, str]:
        """`text` cut before the first DSML opener: the reply text to show, and the block text
        the server parses for us (empty when the text holds none)."""
        at = text.find(dsml.OPEN)
        return (text, "") if at < 0 else (text[:at], text[at:])

    @staticmethod
    def _release_withheld(withheld: str, calls: list) -> str:
        """What becomes of the content withheld from the DSML sentinel on once the reply is
        complete: nothing when the server parsed it into tool calls, the text as written when
        it did not — Keep's own parser then reads what it can."""
        if not withheld:
            return ""
        if calls:
            return ""
        note(f"ds4 left DSML text in the content without tool_calls behind it — the model wrote a block "
             f"the server did not take; the command parser reads it as written ({withheld.count(dsml.OPEN)} opener(s))")
        return withheld

    def create_streaming_completion(self, messages: list[dict], thinking: bool = False,
                                    volatile_tail: int = 0, follow_up: bool = False) -> Generator[str, None, None]:
        """Reasoning deltas (`reasoning_content`) are yielded wrapped in <thinking> tags, the reply
        text after them up to the first DSML sentinel; from there the content is withheld and
        the `tool_calls` deltas (merged by index) are yielded as call text in the active format
        at the end. The reply then goes to the replay store, and the stream's closing usage
        chunk is logged."""
        self._ensure_initialized()
        started = time.monotonic()
        response = self.client.chat.completions.create(**self._request_kwargs(messages, thinking, stream=True))
        in_thinking, reasoning, usage = False, "", None
        reply, pending, withheld = "", "", ""  # text yielded; text held for a split opener; the block text
        calls: dict[int, _ToolCall] = {}
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
                reasoning += thought
                yield thought
            if delta.content:
                if in_thinking:
                    in_thinking = False
                    yield "</thinking>"
                if withheld:
                    withheld += delta.content
                else:
                    visible, withheld = self._split_at_sentinel(pending + delta.content)
                    if withheld:
                        pending = ""
                    else:
                        visible, pending = self._hold_opener_prefix(visible)
                    if visible:
                        reply += visible
                        yield visible
            for entry in getattr(delta, 'tool_calls', None) or []:
                index = getattr(entry, 'index', None) or 0
                calls.setdefault(index, _ToolCall()).merge(entry)
        if in_thinking:
            trace("ds4 stream ended inside the thinking block — closing it")
            yield "</thinking>"
        if pending:
            reply += pending
            yield pending
        if withheld:
            trace(f"ds4 withheld {len(withheld)} chars of DSML text from the reply ({len(calls)} tool call(s) parsed)")
        rendered = self._rendered_calls([calls[i] for i in sorted(calls)])
        tail = self._tail(rendered) or self._release_withheld(withheld, list(calls))
        if tail:
            if reply and not reply.endswith("\n"):
                tail = "\n" + tail
            yield tail
        self._remember(reply + tail, reasoning, reply, rendered)
        self._log_usage(usage, started, len(reasoning))

    @staticmethod
    def _hold_opener_prefix(text: str) -> tuple[str, str]:
        """`text` with any suffix that is a prefix of the DSML opener held back, so an opener
        split over two deltas is still seen whole: (to yield, to hold)."""
        for length in range(min(len(dsml.OPEN) - 1, len(text)), 0, -1):
            if text.endswith(dsml.OPEN[:length]):
                return text[:-length], text[-length:]
        return text, ""

    def create_completion(self, messages: list[dict], thinking: bool = False, volatile_tail: int = 0,
                          follow_up: bool = False) -> str:
        """The reply text, then every parsed tool call written back as DSML text; the model's
        reasoning is logged, not returned."""
        self._ensure_initialized()
        started = time.monotonic()
        response = self.client.chat.completions.create(**self._request_kwargs(messages, thinking, stream=False))
        message = response.choices[0].message
        reasoning = getattr(message, 'reasoning_content', None) or ""
        if reasoning:
            fine(f"ds4 thought:\n{reasoning.strip()}")
        self._log_usage(getattr(response, 'usage', None), started, len(reasoning))
        calls = getattr(message, 'tool_calls', None) or []
        content, withheld = self._split_at_sentinel(message.content or "")
        rendered = self._rendered_calls(calls)
        tail = self._tail(rendered) or self._release_withheld(withheld, calls)
        if tail and content and not content.endswith("\n"):
            tail = "\n" + tail
        self._remember(content + tail, reasoning, content, rendered)
        return content + tail


class _ToolCall:
    """One tool call assembled from stream deltas sharing an index: the name and id are set
    once, the argument text is concatenated."""

    def __init__(self):
        self.id = None
        self.function = _Function()

    def merge(self, delta):
        self.id = self.id or getattr(delta, 'id', None)
        function = getattr(delta, 'function', None)
        if function is not None:
            self.function.name = self.function.name or getattr(function, 'name', None)
            self.function.arguments += getattr(function, 'arguments', None) or ""

    def __repr__(self):
        return f"_ToolCall(id={self.id!r}, name={self.function.name!r}, arguments={self.function.arguments!r})"


class _Function:
    def __init__(self):
        self.name = None
        self.arguments = ""
