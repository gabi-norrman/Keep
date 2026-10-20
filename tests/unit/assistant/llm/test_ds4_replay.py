"""Ds4Replay — Keep's text rows rebuilt into the wire shape ds4-server's live KV cache extends
(the call as `tool_calls` by ds4's id, the result as a tool message, the reasoning replayed),
from the side store a reply fills."""
import json

import pytest

from assistant.commands.formats import DsmlFormat as dsml
from assistant.llm.Ds4Replay import ReplayStore, replay, split_thinking

BLOCK = dsml.render_tool_call("turn_on", {"light_name": "Kitchen lamp"})
BLOCK2 = dsml.render_tool_call("turn_on", {"light_name": "Hall lamp"})
RESULT = "result: \nKitchen lamp is now on."
SYSTEM = {"role": "system", "content": "prompt"}
ASK = {"role": "user", "content": "Turn on the kitchen lamp"}


def _store(path=None) -> ReplayStore:
    store = ReplayStore(path)
    store.remember_reply(BLOCK, "User wants the lamp on.", "", [(BLOCK, "call_1", "turn_on", '{"light_name":"Kitchen lamp"}')])
    return store


class TestReplayOfACallRound:
    def test_the_follow_up_round_sends_the_call_by_id_its_reasoning_and_the_result_as_a_tool_message(self):
        # The in-request round: the assistant row still carries the stream's <thinking> block
        # and the result row is the last message.
        store = _store()
        messages = [SYSTEM, ASK, {"role": "assistant", "content": f"<thinking>User wants the lamp on.</thinking>\n\n{BLOCK}"},
                    {"role": "user", "content": RESULT}]
        out = replay(messages, store)
        assert out[:2] == [SYSTEM, ASK]
        assistant, tool = out[2], out[3]
        assert assistant["reasoning_content"] == "User wants the lamp on."
        assert assistant["content"] == "" and dsml.OPEN not in assistant["content"]
        assert [c["id"] for c in assistant["tool_calls"]] == ["call_1"]
        assert assistant["tool_calls"][0]["function"] == {"name": "turn_on", "arguments": '{"light_name":"Kitchen lamp"}'}
        assert tool == {"role": "tool", "tool_call_id": "call_1", "content": RESULT}
        assert len(out) == 4

    def test_the_next_request_recovers_the_reasoning_of_the_stripped_history_row_from_the_store(self):
        store = _store()
        store.remember_result(BLOCK, RESULT)  # the follow-up round learned it
        out = replay([SYSTEM, ASK, {"role": "assistant", "content": BLOCK}, {"role": "user", "content": RESULT},
                      {"role": "assistant", "content": "It is on."}, {"role": "user", "content": "Thanks"}], store)
        assert out[2]["reasoning_content"] == "User wants the lamp on." and out[2]["tool_calls"][0]["id"] == "call_1"
        assert out[3]["role"] == "tool"
        assert out[4] == {"role": "assistant", "content": "It is on."} and out[5] == {"role": "user", "content": "Thanks"}

    @pytest.mark.parametrize("last", [True, False])
    def test_a_result_row_coalesced_with_the_next_query_is_split_at_the_learned_result(self, last):
        store = _store()
        store.remember_result(BLOCK, RESULT)
        messages = [SYSTEM, ASK, {"role": "assistant", "content": BLOCK},
                    {"role": "user", "content": f"{RESULT}\n\nMarcus (12:01): Thanks. Now the hall lamp."}]
        if not last:
            messages += [{"role": "assistant", "content": "Done."}, {"role": "user", "content": "x"}]
        out = replay(messages, store)
        assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": RESULT}
        assert out[4] == {"role": "user", "content": "Marcus (12:01): Thanks. Now the hall lamp."}
        assert store.result(BLOCK) == RESULT  # a learned result is never replaced by a longer row

    def test_a_result_row_learned_with_a_directive_behind_it_is_relearned_from_the_bare_history_row(self):
        store = _store()
        store.remember_result(BLOCK, f"{RESULT}\n\nReply in English.")
        out = replay([SYSTEM, ASK, {"role": "assistant", "content": BLOCK}, {"role": "user", "content": RESULT},
                      {"role": "user", "content": "next"}], store)
        assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": RESULT}
        assert store.result(BLOCK) == RESULT

    def test_a_result_row_the_store_never_learned_stays_the_user_text_it_is(self):
        store = _store()
        merged = f"{RESULT}\n\nMarcus (12:01): Thanks"
        out = replay([SYSTEM, ASK, {"role": "assistant", "content": BLOCK}, {"role": "user", "content": merged},
                      {"role": "user", "content": "x"}], store)
        assert "tool_calls" in out[2]
        assert out[3] == {"role": "user", "content": merged}

    def test_a_call_row_followed_by_a_plain_query_gets_no_tool_message(self):
        # The command completed silently: no follow-up round, so the next query follows the call.
        out = replay([SYSTEM, ASK, {"role": "assistant", "content": BLOCK}, {"role": "user", "content": "And the hall?"}], _store())
        assert "tool_calls" in out[2] and out[3] == {"role": "user", "content": "And the hall?"}

    def test_several_calls_in_a_round_share_one_tool_message_under_the_first_id(self):
        store = _store()
        store.remember_reply(f"{BLOCK}\n{BLOCK2}", "", "", [(BLOCK, "call_1", "turn_on", "{}"), (BLOCK2, "call_2", "turn_on", "{}")])
        out = replay([ASK, {"role": "assistant", "content": f"{BLOCK}\n{BLOCK2}"}, {"role": "user", "content": RESULT}], store)
        assert [c["id"] for c in out[1]["tool_calls"]] == ["call_1", "call_2"]
        assert out[2]["tool_call_id"] == "call_1" and len(out) == 3

    def test_reply_text_before_the_block_is_the_turns_content(self):
        store = ReplayStore(None)
        reply = f"Turning it on.\n{BLOCK}"
        store.remember_reply(reply, "", "Turning it on.", [(BLOCK, "call_9", "turn_on", "{}")])
        out = replay([ASK, {"role": "assistant", "content": reply}, {"role": "user", "content": RESULT}], store)
        assert out[1]["content"] == "Turning it on." and out[1]["tool_calls"][0]["id"] == "call_9"


class TestRowsLeftAsText:
    def test_a_block_the_store_does_not_know_goes_out_as_the_text_it_is(self):
        out = replay([ASK, {"role": "assistant", "content": BLOCK2}, {"role": "user", "content": RESULT}], _store())
        assert out[1] == {"role": "assistant", "content": BLOCK2} and out[2] == {"role": "user", "content": RESULT}

    def test_text_after_the_block_keeps_the_row_a_text_row(self):
        row = f"{BLOCK}\n[reply interrupted by connection drop]"
        out = replay([ASK, {"role": "assistant", "content": row}], _store())
        assert out[1]["content"] == row and "tool_calls" not in out[1]

    def test_a_text_reply_gets_its_reasoning_and_verbatim_content_back(self):
        store = ReplayStore(None)
        store.remember_reply("\n\nHej!", "A greeting.", "\n\nHej!", [])
        out = replay([ASK, {"role": "assistant", "content": "Hej!"}], store)
        assert out[1] == {"role": "assistant", "content": "\n\nHej!", "reasoning_content": "A greeting."}

    def test_the_rows_own_thinking_block_is_the_reasoning_when_the_store_has_none(self):
        out = replay([ASK, {"role": "assistant", "content": "<thinking>hmm</thinking>Hej!"}], ReplayStore(None))
        assert out[1] == {"role": "assistant", "content": "Hej!", "reasoning_content": "hmm"}

    def test_a_plain_reply_with_nothing_remembered_is_untouched(self):
        store = ReplayStore(None)
        store.remember_reply("Hej!", "", "Hej!", [])
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
        store = _store(path)
        store.remember_result(BLOCK, RESULT)
        reloaded = ReplayStore(path)
        out = replay([ASK, {"role": "assistant", "content": BLOCK},
                      {"role": "user", "content": f"{RESULT}\n\nnext"}, {"role": "user", "content": "x"}], reloaded)
        assert out[1]["tool_calls"][0]["id"] == "call_1" and out[1]["reasoning_content"] == "User wants the lamp on."
        assert out[2]["role"] == "tool" and out[3] == {"role": "user", "content": "next"}

    def test_a_missing_or_broken_file_starts_the_store_empty(self, tmp_path):
        assert ReplayStore(tmp_path / "none.json").turn("x") is None
        broken = tmp_path / "broken.json"
        broken.write_text("{not json")
        store = ReplayStore(broken)
        assert store.call(BLOCK) is None
        store.remember_result(BLOCK, RESULT)  # overwrites the broken file
        assert json.loads(broken.read_text())["results"] == {BLOCK: RESULT}

    def test_the_oldest_entries_leave_when_the_bound_is_reached(self, monkeypatch):
        monkeypatch.setattr(ReplayStore, "LIMIT", 2)
        store = ReplayStore(None)
        for n in range(3):
            store.remember_reply(f"row {n}", f"reasoning {n}", f"row {n}", [])
        assert list(store.turns) == ["row 1", "row 2"]

    def test_remembering_a_row_again_moves_it_to_the_newest_end(self, monkeypatch):
        monkeypatch.setattr(ReplayStore, "LIMIT", 2)
        store = ReplayStore(None)
        store.remember_reply("a", "r", "a", [])
        store.remember_reply("b", "r", "b", [])
        store.remember_reply("a", "r2", "a", [])
        store.remember_reply("c", "r", "c", [])
        assert list(store.turns) == ["a", "c"] and store.turn("a")["reasoning"] == "r2"

    def test_a_call_without_an_id_is_not_remembered_since_ds4_cannot_replay_it(self):
        store = ReplayStore(None)
        store.remember_reply(BLOCK, "", "", [(BLOCK, None, "turn_on", "{}")])
        assert store.call(BLOCK) is None
