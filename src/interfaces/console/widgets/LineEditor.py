"""A text editor with a cursor and a persistent history — the state behind the chat
composer, kept free of any drawing so it can be tested as plain logic. The history walk
(up/down) is filtered by the draft: only entries containing what was typed are visited. The text is one
string; a newline in it is an ordinary character the owner inserts (Shift+Enter in the
chat pane) and the cursor keys walk across. An editor built
with `persist_history=False` keeps no history and never touches TUIState — for fields whose
text must not land in cache/tui_state.json (the Vault tab).

Keys are the names `TUIFloxyConsole._parse_key` produces. Enter, Shift+Enter and Escape are
not handled here: what they mean (send, newline, clear, interrupt) is the owner's call — it
takes the text with `take()`, adds to it with `insert()` or drops it with `clear()`.
"""
from interfaces.console import TUIState
from common.logging import trace
from interfaces.console.framework.TextWidth import normalize

_WORD_BREAK = " \t"


class LineEditor:
    HISTORY_MAX = 200
    STATE_SECTION = "chat"
    # The old Dialog tab saved its history under this name; it is picked up once.
    LEGACY_SECTION = "dialog"

    def __init__(self, persist_history: bool = True):
        self.text = ""
        self.cursor = 0
        self._persist_history = persist_history
        self._history: list[str] = self._load_history() if persist_history else []
        self._history_index = -1   # -1 = editing a fresh line, else the history entry shown
        self._saved_draft = ""

    def _load_history(self) -> list[str]:
        history = TUIState.load(self.STATE_SECTION).get("history")
        if history is None:
            history = TUIState.load(self.LEGACY_SECTION).get("history", [])
            if history:
                trace(f"Chat composer: adopting {len(history)} history entries from the old dialog tab.")
        return [normalize(h) for h in history]

    @property
    def history(self) -> list[str]:
        return list(self._history)

    @property
    def empty(self) -> bool:
        return not self.text

    # --- editing --------------------------------------------------------------------------

    def insert(self, s: str):
        s = normalize(s)   # the composer holds what it shows, cell for cell
        self.text = self.text[:self.cursor] + s + self.text[self.cursor:]
        self.cursor += len(s)
        self._history_index = -1

    def clear(self):
        self.text = ""
        self.cursor = 0
        self._history_index = -1

    def take(self) -> str:
        """The trimmed text, cleared from the editor and remembered in the history (a
        non-persisting editor remembers nothing)."""
        text = self.text.strip()
        self.clear()
        if text and self._persist_history:
            if not self._history or self._history[-1] != text:
                self._history.append(text)
                self._history = self._history[-self.HISTORY_MAX:]
                TUIState.save(self.STATE_SECTION, history=self._history)
        return text

    def _word_start(self) -> int:
        i = self.cursor
        while i > 0 and self.text[i - 1] in _WORD_BREAK:
            i -= 1
        while i > 0 and self.text[i - 1] not in _WORD_BREAK:
            i -= 1
        return i

    def _word_end(self) -> int:
        i = self.cursor
        n = len(self.text)
        while i < n and self.text[i] in _WORD_BREAK:
            i += 1
        while i < n and self.text[i] not in _WORD_BREAK:
            i += 1
        return i

    def handle_key(self, key: str) -> bool:
        """Apply an editing key. True when the key was consumed."""
        if key in ('left',):
            self.cursor = max(0, self.cursor - 1)
        elif key == 'right':
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key in ('home', '\x01'):
            self.cursor = 0
        elif key in ('end', '\x05'):
            self.cursor = len(self.text)
        elif key in ('alt_left', 'ctrl_left'):
            self.cursor = self._word_start()
        elif key in ('alt_right', 'ctrl_right'):
            self.cursor = self._word_end()
        elif key in ('backspace', '\x7f', '\x08'):
            if self.cursor > 0:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
                self._history_index = -1
        elif key == 'delete':
            if self.cursor < len(self.text):
                self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
                self._history_index = -1
        elif key in ('\x17', 'alt_backspace'):   # Ctrl-W: delete the word before the cursor
            start = self._word_start()
            self.text = self.text[:start] + self.text[self.cursor:]
            self.cursor = start
            self._history_index = -1
        elif key == '\x0b':                       # Ctrl-K: kill to the end of the line
            self.text = self.text[:self.cursor]
            self._history_index = -1
        elif key == 'up':
            self._navigate_history(-1)
        elif key == 'down':
            self._navigate_history(1)
        elif len(key) == 1 and key.isprintable():
            self.insert(key)
        else:
            return False
        return True

    # --- history --------------------------------------------------------------------------

    def _navigate_history(self, direction: int):
        """Walk to the next older (direction < 0) or newer entry that contains the draft —
        what the composer held when the walk began, so "//feed" + older recalls the last
        //feed command. Walking past the newest match restores the draft."""
        if not self._history:
            return
        if self._history_index == -1:
            if direction > 0:
                return
            self._saved_draft = self.text
        index = self._next_match(direction)
        if index is None:
            if direction < 0:
                trace(f"Chat composer: no older history entry contains {self._saved_draft!r}")
                return
            self._history_index = -1
            self.text = self._saved_draft
        else:
            self._history_index = index
            self.text = self._history[index]
        self.cursor = len(self.text)

    def _next_match(self, direction: int) -> int | None:
        needle = self._saved_draft.casefold()
        if self._history_index == -1:
            start = len(self._history) - 1
        else:
            start = self._history_index + direction
        indices = range(start, -1, -1) if direction < 0 else range(start, len(self._history))
        return next((i for i in indices if needle in self._history[i].casefold()), None)
