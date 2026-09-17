import pytest

from interfaces.console import TUIState


@pytest.fixture(autouse=True)
def tui_state_file(tmp_path, monkeypatch):
    """Every console test gets its own TUI state file instead of the real one in cache/."""
    path = tmp_path / "tui_state.json"
    monkeypatch.setattr(TUIState, "STATE_FILE", path)
    return path
