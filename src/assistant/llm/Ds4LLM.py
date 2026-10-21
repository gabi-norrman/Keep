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
withheld text with no rendered call behind it (no `tool_calls`, or a delta without a function
name) is released as written for Keep's own parser.

The live KV cache (step 5b of docs/plans/COMMAND_FORMATS_PLAN.md): every request declares one
static tool, so ds4 renders the history in its tool shape and the reasoning of every assistant
turn is replayed in it; on the way out `Ds4Replay` rebuilds Keep's text rows into that shape —
each past call as the `tool_calls` entry with the id ds4 assigned (ds4 then puts the sampled
tokens back itself), its result as a `role: tool` message — from a side store this class fills
after every reply. A follow-up turn then prefills only its own tail instead of the whole prompt.

The replay covers one miss only: the next turn of the same dialog. Any other prompt in between
(another tier, the autonomous check, a second dialog) or a server restart evicts the live
context, and that is the server's own fix — start it with a disk KV cache:

    ./ds4-server --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 16384

The server then saves whole prefixes (the first prompt cut back to its last user turn, every
10k aligned tokens of a long prefill, the live state whenever another prompt evicts it, and at
shutdown), and a request loads the longest saved prefix of its prompt and prefills the rest.
Measured on the test host, 2026-10-20, a 16.5k-token prompt: the switch between two prompts
12.3 s → 0.2 s on V4.1 Flash, 31 s → 0.17 s on GLM 5.2. One saved entry is ~111 MiB on Flash and
~517 MiB on GLM at that length, so 16 GB holds about 140 or 30 of them; the default budget is
4 GB. The two fixes are complementary, not alternatives: without the replay a turn after a
command diverges at the past call, no saved prefix ends there, and the whole history tail
(15–35 KB, roughly 4k–9k tokens, a few seconds) is prefilled again every turn — the disk cache
only brings the fallback down from the whole prompt to that tail.

The "Prefilling…" bar: ds4 says nothing on the wire until the first token (its SSE keepalive is a
bare comment), so the prefill progress is read from the server's log by the class's one
`PrefillTracker`, as Ollama's is — `ds4.log_path` (default `~/Documents/ds4/ds4-server.log`),
over ssh to `ds4.ssh_host` (default: the base_url host) when the server is on another machine.
The log says the workload at once (`prompt start` with `ctx=cached..total:remaining`) but logs a
chunk line only at the backend's chunk boundaries — a cold 26k prompt logs its first at 93%, 16 s
in —, so the bar is mostly the time estimate, refined after each reply from the usage (prompt
tokens minus the cached ones over the time to the first token).
"""
import json
import re
import time
from typing import Generator
from urllib.parse import urlparse

from .ChatGPTLLM import ChatGPTLLM
from .Ds4Replay import ReplayStore, replay
from .OllamaLLM import OllamaLLM
from .PrefillTracker import LogFormat, PrefillTracker, is_local_host, parse_last
from assistant.commands.formats import DsmlFormat as dsml
from assistant.commands.formats.CommandFormats import CommandFormats
from common.ConfigManager import config
from common.logging import info, trace, fine, note


class Ds4LogFormat(LogFormat):
    """The prefill lines of ds4-server's log. A request opens with its workload —
    ds4-server: chat ctx=24576..26339:1763 TOOLS prompt start
    (`ctx=cached..total:remaining`) — then logs a chunk line at each backend chunk boundary, its
    numbers relative to the cached part —
    ds4-server: chat ctx=0..26339:26339 TOOLS prefill chunk 24576/26339 (93.3%) chunk=0.00 t/s avg=1518.73 t/s 16.182s
    — and closes with `prompt done`. A KV restore from disk logs the same chunk line under another phase name.
    """
    def work(self, text: str) -> tuple[int, int] | None:
        match = re.search(r'ctx=(\d+)\.\.(\d+):(\d+)[^\n]*prompt start', text)
        return (int(match.group(1)), int(match.group(3))) if match else None

    def progress(self, text: str) -> tuple[int, int] | None:
        return parse_last(r' chunk (\d+)/(\d+) \(', text)


class Ds4LLM(ChatGPTLLM):
    """A local ds4-server as an LLM backend, with DSML as the command format."""

    DEFAULT_MODEL = 'deepseek-v4.1-flash'
    DEFAULT_BASE_URL = 'http://127.0.0.1:8000/v1'
    DEFAULT_LOG_PATH = '~/Documents/ds4/ds4-server.log'   # where the server's log goes on the host that runs it
    # Measured on V4.1 Flash, cold 24k–30k prompts, 2026-10-21; each reply's usage refines it.
    _prefill = PrefillTracker("ds4", Ds4LogFormat(), seed_tps=1400.0)
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
        Ds4LLM._configure_log_access(self.base_url)

    @classmethod
    def _configure_log_access(cls, base_url: str):
        """Point the prefill tracker at the server's log: the file on this machine when ds4 runs
        here, else over ssh to the serving host (`ds4.ssh_host`, default the base_url host)."""
        host = urlparse(base_url).hostname
        path = config.get('ds4.log_path', cls.DEFAULT_LOG_PATH)
        if is_local_host(host):
            cls._prefill.use_log(path)
        else:
            cls._prefill.use_log(path, ssh_host=config.get('ds4.ssh_host', host))

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

    def prefers_byte_prefix_tail(self) -> bool:
        # ds4 reuses its live context only for a prompt that extends it token for token (its
        # rewind is GLM-only). A trailing directive folded onto the last message is dropped when
        # the next round renders that message as history, so the prefix diverged ~20 tokens
        # before the previous prompt's end and every round fell back to the disk prefix
        # (ds4-server log: `live kv cache miss ... common=<live - 20> reason=token-mismatch`).
        return True

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
    def _log_usage(usage, started: float, reasoning_chars: int = 0) -> int | None:
        """One line per request: prompt tokens with the server's cache hits, output tokens, time.
        Returns the tokens the server prefilled (prompt minus cached), None when it said nothing."""
        if usage is None:
            trace("ds4: response carried no usage")
            return None
        details = getattr(usage, 'prompt_tokens_details', None)
        cached = getattr(details, 'cached_tokens', None) if details is not None else None
        cached_text = f", {cached} cached" if cached is not None else ""
        thought = f", {reasoning_chars} reasoning chars" if reasoning_chars else ""
        info(f"ds4 metrics: ↑ {usage.prompt_tokens} tokens{cached_text} | ↓ {usage.completion_tokens} tokens{thought} "
             f"| {time.monotonic() - started:.2f}s")
        return usage.prompt_tokens - (cached or 0)

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
    def _release_withheld(withheld: str) -> str:
        """The content withheld from the DSML sentinel on, once the reply is complete and no
        parsed call was rendered in its place: the text as written — Keep's own parser then
        reads what it can. The caller passes it only in that case: a `tool_calls` delta
        without a function name renders to nothing, and the text it stood for must not be
        lost with it."""
        if not withheld:
            return ""
        note(f"ds4 left DSML text in the content without a rendered tool call behind it — the model wrote a "
             f"block the server did not take, or handed back a call without a name; the command parser reads "
             f"it as written ({withheld.count(dsml.OPEN)} opener(s))")
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
        token = self._prefill.begin()
        try:
            yield from self._stream(messages, thinking, started, token)
        finally:
            self._prefill.end(token)

    def _stream(self, messages: list[dict], thinking: bool, started: float, token: int | None) -> Generator[str, None, None]:
        response = self.client.chat.completions.create(**self._request_kwargs(messages, thinking, stream=True))
        in_thinking, reasoning, usage = False, "", None
        first_token_at: float | None = None
        reply, pending, withheld = "", "", ""  # text yielded; text held for a split opener; the block text
        calls: dict[int, _ToolCall] = {}
        for chunk in response:
            if getattr(chunk, 'usage', None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if first_token_at is None and (delta.content or getattr(delta, 'reasoning_content', None)
                                           or getattr(delta, 'tool_calls', None)):
                first_token_at = time.monotonic()
                self._prefill.first_token(token)
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
        tail = self._tail(rendered) or self._release_withheld(withheld)
        if tail:
            if reply and not reply.endswith("\n"):
                tail = "\n" + tail
            yield tail
        self._remember(reply + tail, reasoning, reply, rendered)
        prefilled = self._log_usage(usage, started, len(reasoning))
        if prefilled is not None and first_token_at is not None:
            # The time to the first token is the prefill plus one decode step (and any wait in
            # the server's queue, which only makes the next bar a little slow).
            self._prefill.observe_rate(prefilled, first_token_at - started)

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
        token = self._prefill.begin()
        try:
            response = self.client.chat.completions.create(**self._request_kwargs(messages, thinking, stream=False))
        finally:
            self._prefill.end(token)
        message = response.choices[0].message
        reasoning = getattr(message, 'reasoning_content', None) or ""
        if reasoning:
            fine(f"ds4 thought:\n{reasoning.strip()}")
        self._log_usage(getattr(response, 'usage', None), started, len(reasoning))
        calls = getattr(message, 'tool_calls', None) or []
        content, withheld = self._split_at_sentinel(message.content or "")
        rendered = self._rendered_calls(calls)
        tail = self._tail(rendered) or self._release_withheld(withheld)
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
