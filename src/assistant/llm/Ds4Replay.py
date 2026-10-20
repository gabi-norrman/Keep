"""Replays Keep's text history to ds4-server in the wire shape that extends its live KV cache: each past call as the `tool_calls` entry ds4 gave an id, its result as a `role: tool` message, each assistant turn with its `reasoning_content`.

ds4-server keeps one live KV context — the last prompt and the reply it sampled — and reuses
it only for a prompt that extends it token for token; anything else is a full prefill (~7 s
per 5k tokens on the test host). Keep's history is text: the assistant row is the reply with
the thinking removed and the call written back as a DSML block, the result a `result:` row on
the user side. Sent as text, the block never matches what the model sampled. Sent as a
`tool_calls` entry carrying the id ds4 assigned, ds4 puts the sampled tokens back itself (its
"exact DSML tool replay", keyed by that id); with the reasoning replayed in the turn and the
result as a tool message, the prompt extends the context and only the new tail is prefilled.

The store keeps, per reply, what Keep's rows cannot hold, keyed by the texts the history
renders so the conversion needs nothing from Keep but the rows: `turns` — the reasoning and
the verbatim content behind an assistant row (the row is stripped, the model's content may
not have been); `calls` — the id, name and arguments behind each rendered block; `results` —
the `result:` row of a round, learned when it is the last message of a request (the
follow-up round) so a later request that coalesced it with the next query is split back. It
is saved to one small JSON file (`cache/ds4_replay.json`) after every reply, so a server
restart costs no prefill; each map is bounded and the oldest entries go first. A row the
store does not know goes out as the text it is — the model still reads it, the cache misses
once, and the next request is consistent again.
"""
import json
import os
import re
from pathlib import Path

from common.logging import info, trace, note
from common.Paths import CACHE_DIR

from assistant.commands.formats import DsmlFormat as dsml

DEFAULT_PATH = CACHE_DIR / "ds4_replay.json"
_THINKING_RE = re.compile(r"\A\s*<thinking>(.*?)</thinking>", re.S)
RESULT_SEPARATOR = "\n\n"  # what MessageHistory puts between a coalesced result row and the next query


class ReplayStore:
    """The side store: three bounded text-keyed maps, mirrored to a JSON file when a path is
    given (None keeps it in memory — tests)."""

    LIMIT = 400

    def __init__(self, path: Path | None = DEFAULT_PATH):
        self.path = path
        self.turns: dict[str, dict] = {}    # assistant row (stripped) → {"reasoning", "content"}
        self.calls: dict[str, dict] = {}    # rendered block text → {"id", "name", "arguments"}
        self.results: dict[str, str] = {}   # first block text of a round → its result row text
        self._loaded = path is None

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.turns, self.calls, self.results = data.get("turns", {}), data.get("calls", {}), data.get("results", {})
            info(f"ds4 replay store loaded from {self.path}: {len(self.turns)} turn(s), {len(self.calls)} call(s), "
                 f"{len(self.results)} result(s)")
        except FileNotFoundError:
            trace(f"ds4 replay store: no {self.path} yet — starting empty")
        except (OSError, ValueError) as e:
            note(f"ds4 replay store: {self.path} unreadable ({e}) — starting empty")

    def save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"turns": self.turns, "calls": self.calls, "results": self.results},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            note(f"ds4 replay store: could not write {self.path} ({e}) — kept in memory only")

    @classmethod
    def _put(cls, store: dict, key: str, value):
        store.pop(key, None)
        store[key] = value
        while len(store) > cls.LIMIT:
            del store[next(iter(store))]

    def remember_reply(self, reply: str, reasoning: str, content: str, calls: list[tuple[str, str, str, str]]):
        """One finished reply: `reply` the text Keep stores as the assistant row (thinking
        left out, as yielded), `reasoning` the model's, `content` its reply text as ds4 will
        render it (verbatim for a text reply; before the call block, trailing whitespace
        trimmed, for a call), `calls` the `(rendered block text, id, name, arguments)` of each
        parsed call."""
        self._ensure_loaded()
        key = reply.strip()
        changed = False
        if key and (reasoning or calls or content != key):
            self._put(self.turns, key, {"reasoning": reasoning, "content": content})
            changed = True
        for block, call_id, name, arguments in calls:
            if call_id:
                self._put(self.calls, block, {"id": call_id, "name": name, "arguments": arguments})
                changed = True
        if not changed:
            trace("ds4 replay store: a plain reply with nothing to remember")
            return
        trace(f"ds4 replay store: reply remembered ({len(reasoning)} reasoning chars, {len(calls)} call(s))")
        self.save()

    def remember_result(self, block: str, text: str):
        self._ensure_loaded()
        if self.results.get(block) != text:
            self._put(self.results, block, text)
            trace(f"ds4 replay store: result row remembered for a round ({len(text)} chars)")
            self.save()

    def turn(self, key: str) -> dict | None:
        self._ensure_loaded()
        return self.turns.get(key)

    def call(self, block: str) -> dict | None:
        self._ensure_loaded()
        return self.calls.get(block)

    def result(self, block: str) -> str | None:
        self._ensure_loaded()
        return self.results.get(block)


def split_thinking(content: str) -> tuple[str | None, str]:
    """A row's leading `<thinking>…</thinking>` block (the reasoning as the stream wrapped it,
    still in the row within the request that produced it) and the rest."""
    match = _THINKING_RE.match(content)
    if not match:
        return None, content
    return match.group(1), content[match.end():]


def blocks(text: str) -> list[tuple[int, int]]:
    """The `(start, end)` spans of the DSML blocks in a text, in order."""
    spans, at = [], 0
    while (start := text.find(dsml.OPEN, at)) >= 0 and (end := text.find(dsml.CLOSE, start)) >= 0:
        spans.append((start, end + len(dsml.CLOSE)))
        at = end + len(dsml.CLOSE)
    return spans


def replay(messages: list[dict], store: ReplayStore) -> list[dict]:
    """The messages with every assistant row the store knows rebuilt into the wire shape:
    its reasoning as `reasoning_content`, its content as the model wrote it, its DSML blocks
    as `tool_calls` with ds4's ids — and, when every block is known, the `result:` row after it
    as a `role: tool` message (a coalesced next query split off behind it). A row the store
    does not know is passed as it is."""
    out, i, replayed = [], 0, 0
    while i < len(messages):
        message = messages[i]
        if message.get("role") != "assistant" or not isinstance(message.get("content"), str):
            out.append(message)
            i += 1
            continue
        reasoning_in_row, body = split_thinking(message["content"])
        turn = store.turn(body.strip())
        reasoning = turn["reasoning"] if turn else reasoning_in_row
        spans = blocks(body)
        calls = [store.call(body[start:end]) for start, end in spans]
        structured = bool(spans) and all(calls) and not body[spans[-1][1]:].strip()
        if structured:
            content = turn["content"] if turn else body[:spans[0][0]].rstrip()
            rebuilt = {"role": "assistant", "content": content,
                       "tool_calls": [{"id": c["id"], "type": "function",
                                       "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]}
        else:
            rebuilt = {"role": "assistant", "content": turn["content"] if turn and not spans else body}
        if reasoning:
            rebuilt["reasoning_content"] = reasoning
        out.append(rebuilt)
        replayed += bool(turn or structured)
        i += 1
        if structured and i < len(messages):
            result, rest = _result_after(messages, i, body[spans[0][0]:spans[0][1]], store)
            if result is not None:
                out.append({"role": "tool", "tool_call_id": calls[0]["id"], "content": result})
                if rest:
                    out.append({"role": "user", "content": rest})
                i += 1
    if replayed:
        trace(f"ds4 replay: {replayed} assistant row(s) rebuilt into the wire shape")
    return out


def _result_after(messages: list[dict], i: int, block: str, store: ReplayStore) -> tuple[str | None, str | None]:
    """The `result:` row following a replayed call at `messages[i]`: `(result text, the next
    query coalesced behind it or None)`, or `(None, None)` when the row is not one. The first
    time the row is seen it is the last message of a request — the follow-up round's result —
    and is learned whole; a later request renders it as a history row, alone or with the next
    query behind it, and the learned text says where it ends."""
    message = messages[i]
    text = message.get("content")
    if message.get("role") != "user" or not isinstance(text, str) or not text.startswith("result:"):
        return None, None
    learned = store.result(block)
    if learned is None:
        if i == len(messages) - 1:
            store.remember_result(block, text)
            return text, None
        trace("ds4 replay: a result row with no learned text behind the call — sent as the user text it is")
        return None, None
    if text == learned:
        return text, None
    if text.startswith(learned + RESULT_SEPARATOR):
        return learned, text[len(learned) + len(RESULT_SEPARATOR):]
    if learned.startswith(text + RESULT_SEPARATOR):
        # The round learned the row with a request's trailing directive behind it; the history row is the result alone.
        store.remember_result(block, text)
        return text, None
    trace("ds4 replay: the row after a call no longer starts with its learned result — sent as the user text it is")
    return None, None
