import json

from interfaces.console import TUIState


def test_sections_are_independent_and_merge_on_save(tui_state_file):
    assert TUIState.load("log") == {}
    TUIState.save("log", search_text="abc")
    TUIState.save("dialog", history=["hi"])
    TUIState.save("log", mask_active=True)
    assert TUIState.load("log") == {"search_text": "abc", "mask_active": True}
    assert TUIState.load("dialog") == {"history": ["hi"]}
    assert set(json.loads(tui_state_file.read_text())) == {"log", "dialog"}


def test_corrupt_file_reads_as_empty_and_is_replaced_on_save(tui_state_file):
    tui_state_file.write_text("{oops")
    assert TUIState.load("log") == {}
    TUIState.save("log", search_text="x")
    assert json.loads(tui_state_file.read_text()) == {"log": {"search_text": "x"}}
