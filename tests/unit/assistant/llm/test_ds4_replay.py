"""Ds4Replay — Keep's stripped assistant rows sent back to ds4-server as the model wrote them,
so the server's thinking checkpoint continues its live context, from the side store a reply fills."""
import json

import pytest

from assistant.llm.Ds4Replay import ReplayStore, replay, split_thinking

SYSTEM = {"role": "system", "content": "prompt"}
ASK = {"role": "user", "content": "Turn on the kitchen lamp"}
RESULT = "result: \nKitchen lamp is now on."


class TestVerbatimRows:
    def test_a_stored_verbatim_reply_replaces_the_stripped_history_row(self):
        store = ReplayStore(None)
        store.remember_reply("\n\nturn_on(\"Kitchen lamp\")", "Marcus wants the lamp on.", "\n\nturn_on(\"Kitchen lamp\")")
        out = replay([SYSTEM, ASK, {"role": "assistant", "content": 'turn_on("Kitchen lamp")'},
                      {"role": "user", "content": RESULT}], store)
        assert out[:2] == [SYSTEM, ASK] and out[3] == {"role": "user", "content": RESULT}
        assert out[2] == {"role": "assistant", "content": "\n\nturn_on(\"Kitchen lamp\")",
                          "reasoning_content": "Marcus wants the lamp on."}

    def test_the_in_request_row_with_its_thinking_block_goes_out_verbatim_with_the_stored_reasoning(self):
        store = ReplayStore(None)
        store.remember_reply("\n\nHej!", "A greeting.", "\n\nHej!")
        out = replay([ASK, {"role": "assistant", "content": "<thinking>A greeting.</thinking>\n\nHej!"}], store)
        assert out[1] == {"role": "assistant", "content": "\n\nHej!", "reasoning_content": "A greeting."}

    def test_a_row_whose_stored_content_is_not_its_text_goes_out_as_keep_holds_it(self):
        # A call the server parsed out of the reply: the store holds the text before it only.
        store = ReplayStore(None)
        row = 'Turning it on.\nturn_on("Kitchen lamp")'
        store.remember_reply(row, "On it.", "Turning it on.")
        out = replay([ASK, {"role": "assistant", "content": row}], store)
        assert out[1] == {"role": "assistant", "content": row, "reasoning_content": "On it."}

    def test_no_row_turns_into_tool_calls_or_tool_messages(self):
        store = ReplayStore(None)
        store.remember_reply('turn_on("Kitchen lamp")', "r", '\nturn_on("Kitchen lamp")')
        out = replay([ASK, {"role": "assistant", "content": 'turn_on("Kitchen lamp")'},
                      {"role": "user", "content": RESULT}], store)
        assert all("tool_calls" not in m for m in out) and all(m["role"] != "tool" for m in out)
        assert out[2] == {"role": "user", "content": RESULT}


class TestRowsLeftAsText:
    def test_a_row_the_store_does_not_know_goes_out_as_the_text_it_is(self):
        messages = [ASK, {"role": "assistant", "content": "It is on."}, {"role": "user", "content": RESULT}]
        assert replay(messages, ReplayStore(None)) == messages

    def test_the_rows_own_thinking_block_is_the_reasoning_when_the_store_has_none(self):
        out = replay([ASK, {"role": "assistant", "content": "<thinking>hmm</thinking>Hej!"}], ReplayStore(None))
        assert out[1] == {"role": "assistant", "content": "Hej!", "reasoning_content": "hmm"}

    def test_a_plain_reply_with_nothing_to_remember_leaves_the_store_and_the_messages_alone(self):
        store = ReplayStore(None)
        store.remember_reply("Hej!", "", "Hej!")
        assert store.turns == {}
        messages = [ASK, {"role": "assistant", "content": "Hej!"}, {"role": "user", "content": [{"type": "text", "text": "x"}]}]
        assert replay(messages, store) == messages

    @pytest.mark.parametrize("content, expected", [
        ("<thinking>a</thinking>b", ("a", "b")), ("  <thinking>a\nb</thinking>\n\nc", ("a\nb", "\n\nc")),
        ("no block", (None, "no block")), ("text <thinking>late</thinking>", (None, "text <thinking>late</thinking>")),
    ])
    def test_split_thinking_takes_only_a_leading_block(self, content, expected):
        assert split_thinking(content) == expected


class TestStore:
    def test_the_store_survives_a_restart_through_its_file(self, tmp_path):
        path = tmp_path / "ds4_replay.json"
        ReplayStore(path).remember_reply("\n\nIt is on.", "Done.", "\n\nIt is on.")
        out = replay([ASK, {"role": "assistant", "content": "It is on."}], ReplayStore(path))
        assert out[1] == {"role": "assistant", "content": "\n\nIt is on.", "reasoning_content": "Done."}

    def test_a_file_from_the_tool_shape_era_still_loads_its_turns(self, tmp_path):
        path = tmp_path / "ds4_replay.json"
        path.write_text(json.dumps({"turns": {"Hej!": {"reasoning": "r", "content": "\n\nHej!"}},
                                    "calls": {"x": {"id": "call_1"}}, "results": {"x": "y"}}))
        assert ReplayStore(path).turn("Hej!") == {"reasoning": "r", "content": "\n\nHej!"}

    def test_a_missing_or_broken_file_starts_the_store_empty(self, tmp_path):
        assert ReplayStore(tmp_path / "none.json").turn("x") is None
        broken = tmp_path / "broken.json"
        broken.write_text("{not json")
        store = ReplayStore(broken)
        assert store.turn("Hej!") is None
        store.remember_reply("Hej!", "r", "\n\nHej!")  # overwrites the broken file
        assert json.loads(broken.read_text())["turns"]["Hej!"]["content"] == "\n\nHej!"

    def test_the_oldest_entries_leave_when_the_bound_is_reached(self, monkeypatch):
        monkeypatch.setattr(ReplayStore, "LIMIT", 2)
        store = ReplayStore(None)
        for n in range(3):
            store.remember_reply(f"row {n}", f"reasoning {n}", f"row {n}")
        assert list(store.turns) == ["row 1", "row 2"]

    def test_remembering_a_row_again_moves_it_to_the_newest_end(self, monkeypatch):
        monkeypatch.setattr(ReplayStore, "LIMIT", 2)
        store = ReplayStore(None)
        store.remember_reply("a", "r", "a")
        store.remember_reply("b", "r", "b")
        store.remember_reply("a", "r2", "a")
        store.remember_reply("c", "r", "c")
        assert list(store.turns) == ["a", "c"] and store.turn("a")["reasoning"] == "r2"
