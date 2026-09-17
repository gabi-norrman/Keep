"""LineEditor: cursor editing, word keys and the persistent history."""
import json

from interfaces.console import TUIState
from interfaces.console.widgets.LineEditor import LineEditor


def typed(editor: LineEditor, text: str):
    for ch in text:
        assert editor.handle_key(ch)


class TestEditing:
    def test_typing_inserts_at_cursor(self):
        e = LineEditor()
        typed(e, "helo")
        e.handle_key('left')
        e.handle_key('l')
        assert (e.text, e.cursor) == ("hello", 4)

    def test_backspace_and_delete(self):
        e = LineEditor()
        typed(e, "abc")
        e.handle_key('backspace')
        assert e.text == "ab"
        e.handle_key('home')
        e.handle_key('delete')
        assert (e.text, e.cursor) == ("b", 0)
        e.handle_key('delete')
        e.handle_key('delete')
        assert e.text == ""

    def test_home_end_and_emacs_keys(self):
        e = LineEditor()
        typed(e, "abc")
        e.handle_key('\x01')
        assert e.cursor == 0
        e.handle_key('\x05')
        assert e.cursor == 3
        e.handle_key('home')
        assert e.cursor == 0
        e.handle_key('end')
        assert e.cursor == 3

    def test_arrows_stop_at_the_edges(self):
        e = LineEditor()
        typed(e, "ab")
        e.handle_key('right')
        assert e.cursor == 2
        e.handle_key('home')
        e.handle_key('left')
        assert e.cursor == 0

    def test_word_left_and_right(self):
        e = LineEditor()
        typed(e, "turn on  the lights")
        e.handle_key('alt_left')
        assert e.cursor == len("turn on  the ")
        e.handle_key('ctrl_left')
        assert e.cursor == len("turn on  ")
        e.handle_key('alt_right')
        assert e.cursor == len("turn on  the")
        e.handle_key('ctrl_right')
        assert e.cursor == len("turn on  the lights")

    def test_ctrl_w_deletes_the_word_before_the_cursor(self):
        e = LineEditor()
        typed(e, "turn on the lights")
        e.handle_key('\x17')
        assert e.text == "turn on the "
        e.handle_key('alt_backspace')
        assert e.text == "turn on "

    def test_ctrl_k_kills_to_the_end(self):
        e = LineEditor()
        typed(e, "abcdef")
        e.cursor = 3
        e.handle_key('\x0b')
        assert e.text == "abc"

    def test_unknown_keys_are_not_consumed(self):
        e = LineEditor()
        assert not e.handle_key('escape')
        assert not e.handle_key('\r')
        assert not e.handle_key('pageup')
        assert not e.handle_key('\x15')
        assert e.text == ""

    def test_take_strips_and_clears(self):
        e = LineEditor()
        typed(e, "  hi  ")
        assert e.take() == "hi"
        assert e.empty and e.cursor == 0

    def test_take_of_blank_returns_empty_and_records_nothing(self):
        e = LineEditor()
        typed(e, "   ")
        assert e.take() == ""
        assert e.history == []


class TestHistory:
    def test_up_and_down_walk_the_history_and_restore_the_draft(self):
        e = LineEditor()
        typed(e, "one")
        e.take()
        typed(e, "two")
        e.take()
        typed(e, "o")   # both entries contain it
        e.handle_key('up')
        assert (e.text, e.cursor) == ("two", 3)
        e.handle_key('up')
        assert e.text == "one"
        e.handle_key('up')
        assert e.text == "one"
        e.handle_key('down')
        assert e.text == "two"
        e.handle_key('down')
        assert e.text == "o"
        e.handle_key('down')
        assert e.text == "o"

    def test_walk_visits_only_entries_containing_the_draft(self):
        e = LineEditor()
        for text in ["//feed pause all", "hello", "//Feed resume all", "bye"]:
            typed(e, text)
            e.take()
        typed(e, "//feed")
        e.handle_key('up')
        assert e.text == "//Feed resume all"
        e.handle_key('up')
        assert e.text == "//feed pause all"
        e.handle_key('up')
        assert e.text == "//feed pause all"
        e.handle_key('down')
        assert e.text == "//Feed resume all"
        e.handle_key('down')
        assert (e.text, e.cursor) == ("//feed", 6)

    def test_editing_a_recalled_entry_starts_a_new_filtered_walk(self):
        e = LineEditor()
        for text in ["//feed pause all", "//toolkits", "//feed resume all"]:
            typed(e, text)
            e.take()
        typed(e, "//")
        e.handle_key('up')
        assert e.text == "//feed resume all"
        typed(e, "x")
        e.handle_key('up')
        assert e.text == "//feed resume allx"   # nothing contains the edited text

    def test_no_match_leaves_the_draft_alone(self):
        e = LineEditor()
        typed(e, "one")
        e.take()
        typed(e, "zzz")
        e.handle_key('up')
        assert (e.text, e.cursor) == ("zzz", 3)

    def test_consecutive_duplicates_are_recorded_once(self):
        e = LineEditor()
        for _ in range(3):
            typed(e, "same")
            e.take()
        assert e.history == ["same"]

    def test_history_persists_across_editors(self, tui_state_file):
        e = LineEditor()
        typed(e, "remember me")
        e.take()
        assert json.loads(tui_state_file.read_text())["chat"]["history"] == ["remember me"]
        assert LineEditor().history == ["remember me"]

    def test_old_dialog_history_is_adopted_when_no_chat_history_yet(self, tui_state_file):
        TUIState.save("dialog", history=["from the old tab"])
        e = LineEditor()
        assert e.history == ["from the old tab"]
        typed(e, "new")
        e.take()
        assert LineEditor().history == ["from the old tab", "new"]

    def test_history_is_capped(self):
        e = LineEditor()
        for i in range(LineEditor.HISTORY_MAX + 5):
            typed(e, f"q{i}")
            e.take()
        assert len(e.history) == LineEditor.HISTORY_MAX
        assert e.history[-1] == f"q{LineEditor.HISTORY_MAX + 4}"


class TestPersistHistory:
    def test_the_default_editor_loads_and_saves_through_tui_state(self, monkeypatch):
        calls = []
        monkeypatch.setattr(TUIState, "load", lambda section: calls.append(("load", section)) or {})
        monkeypatch.setattr(TUIState, "save", lambda section, **values: calls.append(("save", section)))
        e = LineEditor()
        typed(e, "hi")
        e.take()
        assert ("load", "chat") in calls
        assert ("save", "chat") in calls

    def test_a_non_persisting_editor_never_touches_tui_state(self, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("TUIState must not be touched")
        monkeypatch.setattr(TUIState, "load", forbidden)
        monkeypatch.setattr(TUIState, "save", forbidden)
        e = LineEditor(persist_history=False)
        typed(e, "s3cret")
        assert e.take() == "s3cret"
        assert e.history == []
        e.handle_key('up')   # nothing to recall: the value was not kept anywhere
        assert e.text == ""


class TestNormalizedInput:
    def test_the_composer_holds_what_it_shows(self):
        """A presentation selector or a skin tone is dropped on insert, so the cursor
        arithmetic in characters matches the cells on screen."""
        e = LineEditor()
        e.insert("\u2600\ufe0f")              # sun + VS16
        assert (e.text, e.cursor) == ("\u2600", 1)
        e.insert("\U0001F44D\U0001F3FD")      # thumbs up + skin tone
        assert (e.text, e.cursor) == ("\u2600\U0001F44D", 2)
