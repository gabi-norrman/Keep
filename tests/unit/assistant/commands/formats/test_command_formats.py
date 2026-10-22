"""CommandFormats: the one process-wide choice of command format and who is told of a change."""
from unittest.mock import Mock

import pytest

from assistant.commands.formats.CommandFormats import CommandFormats
from assistant.commands.formats.KeepFormat import KeepFormat
from assistant.commands.formats.QwenFormat import QwenFormat


@pytest.fixture(autouse=True)
def restore():
    yield
    CommandFormats.reset()


def test_set_active_changes_the_format_and_tells_the_listeners():
    seen = []

    def listener(fmt):  # a local function: the registry holds listeners weakly
        seen.append(fmt)

    CommandFormats.on_change(listener)
    assert CommandFormats.set_active(QwenFormat, "a test") is True
    assert CommandFormats.active() is QwenFormat
    assert seen == [QwenFormat]


def test_setting_the_same_format_again_is_no_change():
    seen = []

    def listener(fmt):  # a local function: the registry holds listeners weakly
        seen.append(fmt)

    CommandFormats.on_change(listener)
    CommandFormats.set_active(QwenFormat)
    assert CommandFormats.set_active(QwenFormat) is False
    assert seen == [QwenFormat]


def test_a_value_that_is_not_a_format_class_is_ignored():
    """A DialogManager built over a mocked LLM hands over whatever `command_format()` returned."""
    assert CommandFormats.set_active(Mock()) is False
    assert CommandFormats.set_active(KeepFormat([])) is False
    assert CommandFormats.active() is KeepFormat


def test_a_bound_listener_of_a_dropped_object_is_not_called():
    class Holder:
        def __init__(self):
            self.seen = []

        def changed(self, fmt):
            self.seen.append(fmt)

    kept, gone = Holder(), Holder()
    CommandFormats.on_change(kept.changed)
    CommandFormats.on_change(gone.changed)
    del gone
    import gc
    gc.collect()
    CommandFormats.set_active(QwenFormat)
    assert kept.seen == [QwenFormat]


def test_the_dialog_manager_sets_the_format_from_the_smart_tier(monkeypatch):
    from unittest.mock import patch
    from assistant.DialogManager import DialogManager

    class Smart:
        model = "fake"

        def command_format(self):
            return QwenFormat

    smart = Smart()
    monkeypatch.setattr("assistant.DialogManager.create_llm",
                        lambda name, model=None, **kw: smart if kw.get("prompt_cache") else Mock(model="fake"))
    monkeypatch.setattr("assistant.DialogManager.parse_llm_config", lambda tier: ("FakeLLM", "fake"))
    with patch("assistant.DialogManager.TTFTMonitor"):
        dm = DialogManager(web_request_handler=Mock(), test_suite_mode=True)
    assert CommandFormats.active() is QwenFormat
    assert type(dm.command_processor.format.primary) is QwenFormat  # the processor reads through the rescue wrapper
    dm.command_processor.kill()


def test_the_tier_config_override_beats_the_smart_class_in_the_dialog_manager(monkeypatch):
    """`ai.smart.command_format: keep` on an Ollama smart tier: the process parses Keep's syntax,
    not the Qwen block the class would pick."""
    from unittest.mock import patch
    from assistant.DialogManager import DialogManager

    monkeypatch.setattr("assistant.DialogManager.parse_llm_config", lambda tier: ("OllamaLLM", None))
    monkeypatch.setattr("assistant.DialogManager.command_format_override",
                        lambda tier: "keep" if tier == "smart" else None)
    with patch("assistant.DialogManager.TTFTMonitor"):
        dm = DialogManager(web_request_handler=Mock(), test_suite_mode=True)
    try:
        assert dm.ai_smart.command_format() is KeepFormat
        assert dm.ai_fast.command_format() is QwenFormat
        assert CommandFormats.active() is KeepFormat
        assert type(dm.command_processor.format.primary) is KeepFormat
    finally:
        dm.command_processor.kill()
