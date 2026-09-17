"""The one file where the TUI keeps what it remembers across restarts.

Each tab owns a section named after itself: load() returns it as a dict (empty when nothing
has been saved yet) and save() merges new values into it and rewrites the file."""
import json

from common.Paths import CACHE_DIR
from common.logging import trace

STATE_FILE = CACHE_DIR / "tui_state.json"


def load(section: str) -> dict:
    return _read().get(section, {})


def save(section: str, **values):
    state = _read()
    state.setdefault(section, {}).update(values)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        trace(f"Failed to save TUI state section {section!r}: {e}")


def _read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception as e:
        trace(f"Failed to read TUI state: {e}")
        return {}
