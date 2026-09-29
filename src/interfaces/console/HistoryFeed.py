"""The dialog history in the Home tab chat.

`MessageHistory` is the record of the day: every user turn from every channel, every
assistant reply, and the system entries (home-automation events, prompt-section notes,
startup/shutdown, iMessage traffic). The feed backfills the chat pane with it at startup
and then inserts each new entry as it lands — by timestamp, shown at once, under a dim
header saying who (and through which channel). The console's own turns keep streaming live through
`ConsoleTransport`; the feed recognises the history rows those turns produce and adopts
them instead of showing a second copy:

- a user row with source Console whose text the console just submitted;
- an assistant row that is nothing but command lines — a command round the stream already
  showed as its "Searching X for …" notices — is not shown at all;
- an assistant row that contains the text of a live bubble or Notifier notice shown in
  the last MATCH_WINDOW seconds, or is contained in it. The stored reply keeps the
  command lines the stream strips and the bubble keeps the fallback notice the history
  leaves out, so both are removed before the test — a fallback round that mixed a command
  with its prose once had each side holding text the other lacked and showed twice — and
  containment either way, on tag-stripped text, covers the transport notices;
- assistant rows that land while a console reply is still streaming are held back until
  the reply is over, because the row is written before the last bubble is finalized;
- a system row with source Console is the brief of a turn the server opened here
  (FirstRun) and is not shown at all — the reply it produced is the user's side of it.

The feed is also an `AssistantObserver`: every voice transcription the server hears is
drawn as a system row as it arrives (`on_request_received`, fired by the /stream route
right after the cross-mic vote) — the ones the vote rejects too, with the reason as the
dim detail — since a user row only lands when the whole exchange is stored, seconds
later, and a killed or rejected request never gets one. The chat channels' turns already
stream live, so only voice gets the early row.
"""
import html
import re
import threading
import time
from typing import Optional

from assistant.AssistantEvents import AssistantObserver
from assistant.ChannelProfile import SOURCE_CONSOLE, SOURCE_VOICE
from assistant.tools.MessageFormatter import MessageFormatter
from common.logging import info, trace
from interfaces.chat import FallbackNotice
from interfaces.chat.RichText import repair_markdown, strip_tags
from interfaces.console.SystemEvents import markup
from interfaces.console.widgets.ChatPane import (KIND_ASSISTANT, KIND_DIFF, KIND_NOTICE, KIND_SYSTEM, KIND_USER,
                                                 ChatPane)

ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
ROLE_COMMAND = "command"               # a command round's result, kept for the LLM's next turn
ROLE_NOTE = "note"                     # the clock note a byte-prefix request sent before its query
SOURCE_PROMPT_DELTA = "prompt_delta"   # PromptManager's section-change notes, a -/+ listing
SOURCE_TOOLKIT_EVICTED = "toolkit_evicted"   # MessageHistory's stub where a trimmed toolkit row was
SHOW_PROMPT_DELTAS = False             # show the prompt-delta notes in the chat history
SHOW_COMMAND_ROUNDS = False            # show assistant rows that are only command lines
SHOW_COMMAND_RESULTS = False           # show the command result rows (the stream's notices already summarise them)
SHOW_OPENING_BRIEFS = False            # show the system turn behind a conversation the server opened (FirstRun)
COMMAND_LINE = re.compile(r"^\w+\(.*\)$")


def normalize(text: str) -> str:
    """Tag-free, whitespace-collapsed text for comparing what was shown with what was stored.
    The stream swaps every backtick for a space before a chunk reaches an interface
    (DialogManager) while the stored row keeps the model's own words, so drop them here too."""
    return " ".join(strip_tags(text or "").replace("`", " ").split())


def entry_key(entry: dict) -> tuple:
    return (entry["role"], entry["timestamp"], entry["content"])


def is_command_line(line: str) -> bool:
    """A `name(...)` line; a model that quotes the command in backticks still wrote a command."""
    return bool(COMMAND_LINE.match(line.strip().strip("`").strip()))


def only_commands(text: str) -> bool:
    """True when every non-blank line of the (tag-stripped) text is a command."""
    lines = [line for line in strip_tags(text or "").splitlines() if line.strip()]
    return bool(lines) and all(is_command_line(line) for line in lines)


def without_commands(text: str) -> str:
    """The text minus its command lines — what the stream showed of a stored reply."""
    return "\n".join(line for line in strip_tags(text or "").splitlines() if not is_command_line(line))


class HistoryFeed(AssistantObserver):
    MATCH_WINDOW = 120.0   # seconds a live bubble or notice stays a candidate for adoption

    def __init__(self, history, pane: ChatPane):
        self._history = history
        self._pane = pane
        self._lock = threading.Lock()
        self._seen: set = set()
        self._pending_user_texts: list[str] = []   # submitted here, history row not seen yet
        self._adopted: set = set()                 # id() of live entries already matched to a row
        self._in_flight = 0
        self._turn_started_at: Optional[float] = None
        self._deferred: list[dict] = []
        self._wipes = history.wipes

    def start(self):
        with self._lock:
            self._backfill()
        self._history.subscribe(self._on_change)

    def _backfill(self):
        entries = self._history.get_merged_entries()
        for entry in entries:
            self._seen.add(entry_key(entry))
            self._insert(entry)
        info(f"HistoryFeed: backfilled {len(entries)} history entries into the chat")

    def stop(self):
        self._history.unsubscribe(self._on_change)

    # --- the console's own turns (from ConsoleTransport) ------------------------------------

    def user_submitted(self, text: str):
        """The console's own user line is on screen; its history row is adopted, not shown."""
        with self._lock:
            self._pending_user_texts.append(normalize(text))

    def turn_started(self):
        """A console reply is in flight (a typed message, or a turn the server opened)."""
        with self._lock:
            self._in_flight += 1
            if self._turn_started_at is None:
                self._turn_started_at = time.time()

    def turn_finished(self):
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            if self._in_flight:
                trace(f"HistoryFeed: reply finished, {self._in_flight} console turn(s) still in flight")
                return
            since = self._turn_started_at or 0.0
            self._turn_started_at = None
            deferred, self._deferred = self._deferred, []
            if deferred:
                trace(f"HistoryFeed: console reply over, placing {len(deferred)} deferred assistant row(s)")
            for entry in deferred:
                self._place_assistant(entry, since)

    # --- from the assistant (the request's thread) ------------------------------------------

    def on_request_received(self, context, mic="", confidence=None, rejected=None):
        """A voice transcription landed: say so now, under a System header naming the
        location, with the mic, engine and STT confidence, and the vote's verdict when it
        was rejected."""
        if context.source != SOURCE_VOICE:
            trace(f"HistoryFeed: {context.source} request received, its channel shows it itself")
            return
        who = f" from {context.user}" if context.user else ""
        origin = ", ".join(part for part in (f"{mic} mic" if mic else "", context.engine or "",
                                             f"c{confidence:.2f}" if confidence is not None else "") if part)
        text = f"Incoming voice request{who}{f' ({origin})' if origin else ''}: \u201c{context.user_input}\u201d"
        header = MessageFormatter.format_header("System", None, context.source, context.location)
        with self._lock:
            entry = self._pane.insert(KIND_SYSTEM, html.escape(text, quote=False), time.time(), header=header)
            if rejected:
                self._pane.set_detail(entry, f"rejected: {rejected}")
        info(f"HistoryFeed: showed the incoming voice request{who} ({len(context.user_input or '')} chars"
             f"{', rejected: ' + rejected if rejected else ''})")

    # --- from MessageHistory (any thread) ---------------------------------------------------

    def _on_change(self):
        with self._lock:
            if self._history.wipes != self._wipes:
                self._rebuild()
                return
            new = [e for e in self._history.get_merged_entries() if entry_key(e) not in self._seen]
            if not new:
                return
            for entry in new:
                self._seen.add(entry_key(entry))
                self._route(entry)

    def _rebuild(self):
        """Today's history was wiped: start the pane over from what the history still holds."""
        self._wipes = self._history.wipes
        info("HistoryFeed: history wiped, rebuilding the chat from what remains")
        self._pane.clear()
        self._seen.clear()
        self._adopted.clear()
        self._deferred.clear()
        self._backfill()

    def _route(self, entry: dict):
        role = entry["role"]
        if role == ROLE_NOTE:
            trace("HistoryFeed: hiding a clock note row")
            return
        if role == ROLE_COMMAND:
            if not SHOW_COMMAND_RESULTS:
                trace("HistoryFeed: hiding a command result row (SHOW_COMMAND_RESULTS is off)")
                return
            self._insert(entry)
        elif role == ROLE_SYSTEM:
            if entry.get("source") == SOURCE_CONSOLE and not SHOW_OPENING_BRIEFS:
                # A system row from the console itself is the brief of a turn the server
                # opened here (FirstRun); the reply is what the user should see, not it.
                trace("HistoryFeed: hiding the brief of a server-opened turn (SHOW_OPENING_BRIEFS is off)")
                return
            self._insert(entry)
        elif role == ROLE_ASSISTANT:
            if self._in_flight:
                trace("HistoryFeed: assistant row landed during a console reply, deferring it")
                self._deferred.append(entry)
            else:
                self._place_assistant(entry, time.time() - self.MATCH_WINDOW)
        elif entry.get("source") == SOURCE_CONSOLE and normalize(entry["content"]) in self._pending_user_texts:
            self._pending_user_texts.remove(normalize(entry["content"]))
            trace("HistoryFeed: adopted the console's own user turn from history")
        else:
            self._insert(entry)

    def _place_assistant(self, entry: dict, since: float):
        """Show an assistant row unless a live bubble or notice since `since` already did."""
        if not SHOW_COMMAND_ROUNDS and only_commands(entry["content"]):
            trace("HistoryFeed: hiding a command-only assistant row (its commands were shown as notices)")
            return
        # The live bubble went through ChatSession's Markdown repair, the stored row did not.
        stored = normalize(without_commands(repair_markdown(entry["content"])))
        for live in self._pane.entries:
            if live.kind not in (KIND_ASSISTANT, KIND_NOTICE) or live.history:
                continue
            if live.timestamp < since or id(live) in self._adopted:
                continue
            shown = normalize(FallbackNotice.strip(live.body.text))
            if shown and stored and (shown in stored or stored in shown):
                self._adopted.add(id(live))
                trace(f"HistoryFeed: adopted a live {live.kind} entry as the history row ({len(stored)} chars)")
                return
        self._insert(entry)

    def _insert(self, entry: dict):
        role = entry["role"]
        timestamp = entry["timestamp"]
        content = entry["content"]
        if role == ROLE_SYSTEM:
            header = MessageFormatter.format_header("System", None, entry.get("source"), entry.get("location"))
            if entry.get("source") in (SOURCE_PROMPT_DELTA, SOURCE_TOOLKIT_EVICTED):
                # Both describe what the LLM's window holds, not the conversation.
                if not SHOW_PROMPT_DELTAS:
                    trace(f"HistoryFeed: hiding a {entry['source']} note (SHOW_PROMPT_DELTAS is off)")
                    return
                kind = KIND_DIFF
            else:
                kind = KIND_SYSTEM
                content = markup(content)
        elif role == ROLE_ASSISTANT:
            kind = KIND_ASSISTANT
            header = MessageFormatter.format_assistant_header(None)
            content = repair_markdown(content)
        elif role == ROLE_COMMAND:
            kind = KIND_SYSTEM
            header = "Command result"
        else:
            kind = KIND_USER
            header = MessageFormatter.format_header(role.capitalize(), None, entry.get("source"),
                                                    entry.get("location"))
        self._pane.insert(kind, content, timestamp, header=header)
