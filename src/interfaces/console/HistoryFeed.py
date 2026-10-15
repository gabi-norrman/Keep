"""The dialog history in the Home tab chat.

`MessageHistory` is the record of the day: every user turn from every channel, every
assistant reply, and the system entries (home-automation events, prompt-section notes,
startup/shutdown, iMessage traffic). The feed backfills the chat pane with it at startup
and then inserts each new entry as it lands — by timestamp, shown at once, under a dim
header saying who (and through which channel).

A row is drawn the way its stream showed it, whether it is backfilled or lands live: an
assistant row is its prose plus one "Searching for …" notice per command it called
(`StoredReplyReader` — the stored reply keeps the calls with their whole arguments, for the
LLM), and the command result, clock note and opening-brief rows, which no stream ever
showed, are left out.

The console's own turns keep streaming live through `ConsoleTransport`; the feed
recognises the history rows those turns produce and adopts them instead of showing a
second copy:

- a user row with source Console whose text the console just submitted;
- a command of an assistant row whose notice the stream showed in the last MATCH_WINDOW
  seconds;
- the prose of an assistant row that contains the text of a live bubble or Notifier notice
  shown in the last MATCH_WINDOW seconds, or is contained in it. The bubble keeps the
  fallback notice the history leaves out, so it is removed before the test, and
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
import threading
import time
from typing import Optional

from assistant.AssistantEvents import AssistantObserver
from assistant.ChannelProfile import SOURCE_CONSOLE, SOURCE_VOICE
from assistant.commands.StoredReplyReader import StoredReply, StoredReplyReader
from assistant.tools.MessageFormatter import MessageFormatter
from common.logging import info, trace
from interfaces.chat import FallbackNotice
from interfaces.chat.RichText import command_line, repair_markdown, strip_tags
from interfaces.console.SystemEvents import markup
from interfaces.console.widgets.ChatPane import (KIND_ASSISTANT, KIND_DIFF, KIND_NOTICE, KIND_SYSTEM, KIND_USER,
                                                 ChatPane)

ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
ROLE_COMMAND = "command"               # a command round's result, kept for the LLM's next turn
ROLE_NOTE = "note"                     # the clock note a byte-prefix request sent before its query
SOURCE_PROMPT_DELTA = "prompt_delta"   # PromptManager's section-change notes, a -/+ listing
SOURCE_TOOLKIT_EVICTED = "toolkit_evicted"   # MessageHistory's one stub naming the trimmed toolkit references
SHOW_PROMPT_DELTAS = False             # show the prompt-delta notes in the chat history
SHOW_COMMAND_ROUNDS = False            # show an assistant row's command calls as written, not as their notices
SHOW_COMMAND_RESULTS = False           # show the command result rows (what the LLM was handed back)
SHOW_OPENING_BRIEFS = False            # show the system turn behind a conversation the server opened (FirstRun)


def normalize(text: str) -> str:
    """Tag-free, whitespace-collapsed text for comparing what was shown with what was stored.
    The stream swaps every backtick for a space before a chunk reaches an interface
    (DialogManager) while the stored row keeps the model's own words, so drop them here too."""
    return " ".join(strip_tags(text or "").replace("`", " ").split())


def entry_key(entry: dict) -> tuple:
    return (entry["role"], entry["timestamp"], entry["content"])


class HistoryFeed(AssistantObserver):
    MATCH_WINDOW = 120.0   # seconds a live bubble or notice stays a candidate for adoption

    def __init__(self, history, pane: ChatPane):
        self._history = history
        self._pane = pane
        self._reader = StoredReplyReader()
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
        trace(f"HistoryFeed: showed the incoming voice request{who} ({len(context.user_input or '')} chars"
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
        if role == ROLE_ASSISTANT:
            if self._in_flight:
                trace("HistoryFeed: assistant row landed during a console reply, deferring it")
                self._deferred.append(entry)
            else:
                self._place_assistant(entry, time.time() - self.MATCH_WINDOW)
        elif (role not in (ROLE_NOTE, ROLE_COMMAND, ROLE_SYSTEM) and entry.get("source") == SOURCE_CONSOLE
              and normalize(entry["content"]) in self._pending_user_texts):
            self._pending_user_texts.remove(normalize(entry["content"]))
            trace("HistoryFeed: adopted the console's own user turn from history")
        else:
            self._insert(entry)

    def _place_assistant(self, entry: dict, since: float):
        """Show an assistant row, minus what a live bubble or notice since `since` already
        showed of it: the notice of each command first — an exact match — then the prose."""
        reply = self._read(entry)
        before = [echo for echo in reply.before if not self._adopt_echo(echo, since)]
        after = [echo for echo in reply.after if not self._adopt_echo(echo, since)]
        prose = reply.prose
        # The live bubble went through ChatSession's Markdown repair, the stored row did not.
        stored = normalize(repair_markdown(prose))
        if stored and self._adopt(since, lambda shown: shown in stored or stored in shown):
            trace(f"HistoryFeed: adopted a live entry as the history row's prose ({len(stored)} chars)")
            prose = ""
        self._draw(StoredReply(prose, before, after), entry["timestamp"])

    def _adopt_echo(self, echo: str, since: float) -> bool:
        wanted = normalize(command_line(echo))
        if not self._adopt(since, lambda shown: shown == wanted, kinds=(KIND_NOTICE,)):
            return False
        trace(f"HistoryFeed: the stream already showed the command notice {echo!r}")
        return True

    def _adopt(self, since: float, matches, kinds=(KIND_ASSISTANT, KIND_NOTICE)) -> bool:
        """Claim the first live entry since `since` whose shown text `matches`, so that no
        other history row takes it for its own."""
        for live in self._pane.entries:
            if live.kind not in kinds or live.history:
                continue
            if live.timestamp < since or id(live) in self._adopted:
                continue
            shown = normalize(FallbackNotice.strip(live.body.text))
            if shown and matches(shown):
                self._adopted.add(id(live))
                return True
        return False

    def _read(self, entry: dict) -> StoredReply:
        if SHOW_COMMAND_ROUNDS:
            return StoredReply(entry["content"], [], [])
        return self._reader.read(entry["content"])

    def _draw(self, reply: StoredReply, timestamp: float):
        """An assistant row as its stream showed it: a notice per command, the prose as a bubble."""
        if not (reply.prose or reply.before or reply.after):
            trace("HistoryFeed: nothing left of an assistant row to show")
            return
        for echo in reply.before:
            self._pane.insert(KIND_NOTICE, command_line(echo), timestamp)
        if reply.prose:
            self._pane.insert(KIND_ASSISTANT, repair_markdown(reply.prose), timestamp,
                              header=MessageFormatter.format_assistant_header(None))
        for echo in reply.after:
            self._pane.insert(KIND_NOTICE, command_line(echo), timestamp)

    def _insert(self, entry: dict):
        """Draw a history row nothing live has shown — every row of a backfill."""
        role = entry["role"]
        timestamp = entry["timestamp"]
        content = entry["content"]
        if role == ROLE_NOTE:
            trace("HistoryFeed: hiding a clock note row")
            return
        if role == ROLE_COMMAND and not SHOW_COMMAND_RESULTS:
            trace("HistoryFeed: hiding a command result row (SHOW_COMMAND_RESULTS is off)")
            return
        if role == ROLE_ASSISTANT:
            self._draw(self._read(entry), timestamp)
            return
        if role == ROLE_SYSTEM:
            if entry.get("source") == SOURCE_CONSOLE and not SHOW_OPENING_BRIEFS:
                # A system row from the console itself is the brief of a turn the server
                # opened here (FirstRun); the reply is what the user should see, not it.
                trace("HistoryFeed: hiding the brief of a server-opened turn (SHOW_OPENING_BRIEFS is off)")
                return
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
        elif role == ROLE_COMMAND:
            kind = KIND_SYSTEM
            header = "Command result"
        else:
            kind = KIND_USER
            header = MessageFormatter.format_header(role.capitalize(), None, entry.get("source"),
                                                    entry.get("location"))
        self._pane.insert(kind, content, timestamp, header=header)
