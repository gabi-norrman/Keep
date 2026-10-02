"""memory/watchlist/ — everything the assistant is keeping an eye on, one small file per open item.

An item is something with a state that will change or end: an order on its way, an invoice to
pay, an appointment, an open decision, a reminder. It answers to its name, like a procedure
does. Its note is the item's *current* state and is replaced when the item is saved again, so
an item lives in one place instead of as a trail of remembered sentences. Deleting an item
removes its file; every replaced or deleted version is copied to `log/memory/watchlist/`
first, which is a debugging trail the LLM never reads.

An item may carry a check cadence (`every`: "1d", "6h", "weekly" — free text the LLM reads)
and always carries when it was last looked at (`checked`, with the time of day): `updated`
moves when the state changes, `checked` whenever someone looks (the assistant's `save_watch` is a
look; the curator's nightly rewrite is not). A check that finds nothing
stamps `checked` with `checked_watch` and leaves the note alone, so the note never turns into
a trail again. Whether a check is due is the LLM's arithmetic, not the code's: the prompt shows
the cadence and the stamp side by side. `interval` and `stamp` read the two for the TUI alone,
which draws when the next check falls; nothing the LLM is told depends on them.

The `save_watch`, `checked_watch` and `delete_watch` commands and the memory curator's command
block all go through `run`. The prompt's watch-list section is only an index (`render_index`: a
line per item); `load_watch` returns whole items (`load`), and the readers that work through the
whole list — the autonomous check, the curator — get `render`. The TUI's Watch list tab draws
the items themselves (`items`).
"""
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from common.logging import info, trace
from common.Paths import MEMORY_DIR, MEMORY_LOG_DIR

WATCHLIST_DIR = MEMORY_DIR / "watchlist"
LOG_DIR = MEMORY_LOG_DIR / "watchlist"
COMMANDS = ("save_watch", "checked_watch", "delete_watch")
# An item nobody has touched this long, or whose due date passed this long ago, is marked
# STALE in the prompt: the assistant and the curator then know which ones to look at.
STALE_AFTER_DAYS = 14
OVERDUE_AFTER_DAYS = 2
EMPTY = "Nothing is being watched."
# The header of a loaded item, as `load_watch(name)` returns it.
HEADER_PREFIX = "# Watch item: "
# How much of an item's note its index line carries.
INDEX_NOTE_CHARS = 80

lock = threading.RLock()


@dataclass
class Item:
    name: str
    note: str = ""
    due: str = ""
    created: str = ""
    updated: str = ""
    every: str = ""
    checked: str = ""

    def text(self, closing: str = "") -> str:
        header = [f"# {self.name}", f"Due: {self.due or '-'}", f"Every: {self.every or '-'}",
                  f"Created: {self.created}", f"Updated: {self.updated}", f"Checked: {self.checked or '-'}"]
        return "\n".join(header) + (f"\n{closing}" if closing else "") + f"\n\n{self.note.strip()}\n"


def slug(name: str) -> str:
    """The file name an item answers to: 'TV delivery' and "tv-delivery" are the same one."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().strip("\"'`#").lower()).strip("-")


def _path(name: str, directory: Path) -> Path:
    return directory / f"{slug(name)}.md"


def _parse(path: Path) -> Item | None:
    """The item a file holds; None for an unreadable file."""
    try:
        header, _, note = path.read_text(encoding="utf-8").partition("\n\n")
    except OSError as e:
        trace(f"Watch list: cannot read {path.name}: {e}")
        return None
    fields = {"due": "", "created": "", "updated": "", "every": "", "checked": ""}
    for line in header.splitlines():
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            if key.lower() in fields:
                fields[key.lower()] = value.strip()
    for key in ("due", "every", "checked"):
        if fields[key] == "-":
            fields[key] = ""
    return Item(name=path.stem, note=note.strip(), **fields)


def items(directory: Path = None) -> list[Item]:
    """The open items, oldest first."""
    directory = directory or WATCHLIST_DIR
    with lock:
        if not directory.is_dir():
            return []
        found = [item for path in directory.glob("*.md") if (item := _parse(path)) is not None]
    return sorted(found, key=lambda item: (item.created, item.name))


def get(name: str, directory: Path = None) -> Item | None:
    directory = directory or WATCHLIST_DIR
    if not slug(name):
        return None
    path = _path(name, directory)
    with lock:
        return _parse(path) if path.is_file() else None


def _log_copy(item: Item, log_dir: Path, closing: str = ""):
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    (log_dir / f"{item.name}.{stamp}.md").write_text(item.text(closing), encoding="utf-8")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _given(value: str | None) -> str | None:
    """A header value as the caller meant it: None (or blank) keeps the old one, '-' clears it."""
    return None if value is None or not value.strip() else value.strip()


def save(name: str, note: str, due: str | None = None, directory: Path = None, log_dir: Path = None,
         every: str | None = None, looked: bool = True) -> Item | None:
    """Start watching `name`, or replace the note of the item that already answers to it. The
    due date and the check cadence are kept unless given ('-' clears either). The assistant's
    save is a look at the item, so it stamps `checked` too; the curator's nightly rewrite is
    not one (`looked=False`) and leaves the stamp alone, or the next check would be postponed
    by a whole cadence. None when the name has no letters or digits."""
    directory = directory or WATCHLIST_DIR
    if not slug(name):
        return None
    today = date.today().isoformat()
    due, every = _given(due), _given(every)
    with lock:
        item = get(name, directory)
        if item is None:
            item = Item(slug(name), note.strip(), "" if due in (None, "-") else due, today, today,
                        "" if every in (None, "-") else every)
            directory.mkdir(parents=True, exist_ok=True)
            info(f"Watch list: '{item.name}' added" + (f", due {item.due}" if item.due else "")
                 + (f", checked every {item.every}" if item.every else ""))
        else:
            _log_copy(item, log_dir or LOG_DIR)
            item.note = note.strip()
            if due is not None:
                item.due = "" if due == "-" else due
            if every is not None:
                item.every = "" if every == "-" else every
            item.updated = today
            info(f"Watch list: '{item.name}' updated" + (f", checked every {item.every}" if item.every else ""))
        if looked:
            item.checked = _now()
        else:
            trace(f"Watch list: '{item.name}' saved without a look — checked stays {item.checked or 'unset'}")
        _path(name, directory).write_text(item.text(), encoding="utf-8")
    return item


def check(name: str, directory: Path = None) -> Item | None:
    """Record that the item was looked at just now and nothing changed: only `checked` moves,
    the note and `updated` stay. None when there is no such item."""
    directory = directory or WATCHLIST_DIR
    with lock:
        item = get(name, directory)
        if item is None:
            info(f"Watch list: no item '{slug(name) or name.strip()}' to mark checked")
            return None
        item.checked = _now()
        _path(name, directory).write_text(item.text(), encoding="utf-8")
    info(f"Watch list: '{item.name}' checked, nothing new" + (f" (every {item.every})" if item.every else ""))
    return item


def delete(name: str, outcome: str = "", directory: Path = None, log_dir: Path = None) -> Item | None:
    """Remove the item's file, its last state and the outcome kept in the log; None when there is no such item."""
    directory = directory or WATCHLIST_DIR
    with lock:
        item = get(name, directory)
        if item is None:
            info(f"Watch list: no item '{slug(name) or name.strip()}' to delete")
            return None
        _log_copy(item, log_dir or LOG_DIR,
                  closing=f"Closed: {date.today().isoformat()}\nOutcome: {outcome.strip() or '-'}")
        _path(name, directory).unlink()
    info(f"Watch list: '{item.name}' deleted" + (f" — {outcome.strip()}" if outcome.strip() else ""))
    return item


def _days_since(text: str, today: date) -> int | None:
    """Days from the ISO date `text` opens with to today; None when it opens with none."""
    match = re.match(r"\d{4}-\d{2}-\d{2}", text.strip())
    if not match:
        return None
    try:
        return (today - date.fromisoformat(match.group(0))).days
    except ValueError:
        return None


_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400, "mo": 30 * 86400}
_UNIT_WORDS = (("mo", "mo|months?"), ("m", "m|mins?|minutes?"), ("h", "h|hrs?|hours?"), ("d", "d|days?"),
               ("w", "w|wks?|weeks?"))
_UNIT_PATTERN = "|".join(f"(?P<{unit}>{words})" for unit, words in _UNIT_WORDS)
_AMOUNT = re.compile(rf"(\d+(?:\.\d+)?)\s*(?:{_UNIT_PATTERN})\b")
_BARE_UNIT = re.compile(r"\b(?:(?P<h>hour(?:ly)?)|(?P<d>da(?:il)?y|night(?:ly)?)|(?P<w>week(?:ly)?)|(?P<mo>month(?:ly)?))\b")


def interval(every: str) -> timedelta | None:
    """The time between two checks a cadence asks for: "6h", "1d 12h", "weekly", "every 2 days",
    "every day". None for an empty cadence or one this cannot read — it is free text."""
    text = every.strip().lower()
    seconds = sum(float(m.group(1)) * _UNIT_SECONDS[m.lastgroup] for m in _AMOUNT.finditer(text))
    if not seconds and (bare := _BARE_UNIT.search(text)):
        seconds = _UNIT_SECONDS[bare.lastgroup]
    return timedelta(seconds=seconds) if seconds > 0 else None


def stamp(text: str) -> datetime | None:
    """The moment a header value names: `checked`'s date and time, or a bare date (midnight).
    None when it opens with neither."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", text.strip())
    if not match:
        return None
    try:
        return datetime.fromisoformat(f"{match.group(1)} {match.group(2) or '00:00'}")
    except ValueError:
        return None


def staleness(item: Item, today: date = None) -> str:
    """Why the item needs a look, or '' when it does not."""
    today = today or date.today()
    overdue = _days_since(item.due, today)
    if overdue is not None and overdue >= OVERDUE_AFTER_DAYS:
        return f"STALE: due date passed {overdue} days ago"
    idle = _days_since(item.updated, today)
    if idle is not None and idle >= STALE_AFTER_DAYS:
        return f"STALE: no update for {idle} days"
    return ""


def _block(item: Item, today: date = None) -> str:
    """One item in full: its name and facts on a line, the note under it."""
    facts = ([f"due {item.due}"] if item.due else []) + [f"updated {item.updated}"]
    if item.every:
        facts.append(f"check every {item.every}")
    if item.checked:
        facts.append(f"last checked {item.checked}")
    if stale := staleness(item, today):
        facts.append(stale)
    return f"`{item.name}` ({', '.join(facts)})" + (f"\n{item.note}" if item.note else "")


def render(directory: Path = None, today: date = None) -> str:
    """The open items in full, as the autonomous check and the curator read them."""
    return "\n\n".join(_block(item, today) for item in items(directory)) or EMPTY


def _note_start(note: str) -> str:
    """The start of a note, cut at a word once it runs past what an index line carries."""
    first = " ".join(note.strip().split("\n", 1)[0].split())
    if len(first) <= INDEX_NOTE_CHARS:
        return first
    return first[:INDEX_NOTE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:—-(") + "…"


def render_index(directory: Path = None, today: date = None) -> str:
    """The `{WATCHLIST}` text: one line per open item — its name, due date, staleness and the
    start of its note. Neither the cadence nor the check stamp, so a poll that only stamps an
    item leaves the prompt as it was."""
    lines = []
    for item in items(directory):
        facts = ([f"due {item.due}"] if item.due else [])
        if stale := staleness(item, today):
            facts.append(stale)
        start = _note_start(item.note)
        lines.append(f"- `{item.name}`" + (f" ({', '.join(facts)})" if facts else "") + (f" — {start}" if start else ""))
    return "\n".join(lines) or EMPTY


def load(name: str, directory: Path = None, today: date = None) -> str:
    """What `load_watch(name)` answers: the item in full, or which items exist instead."""
    item = get(name, directory)
    if item is None:
        info(f"Watch list: '{name.strip()}' asked for, no such item")
        open_names = ", ".join(i.name for i in items(directory)) or "none"
        return f"load_watch failed: no item named '{slug(name) or name.strip()}'. Open: {open_names}"
    trace(f"Watch list: '{item.name}' loaded ({len(item.note)} chars)")
    return f"{HEADER_PREFIX}{_block(item, today)}\n"


def version(directory: Path = None) -> tuple:
    """Changes whenever the rendered list would: on any save or delete, and at midnight
    (staleness is counted in days)."""
    directory = directory or WATCHLIST_DIR
    try:
        files = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in directory.glob("*.md")))
    except OSError:
        files = ()
    return files, time.strftime("%Y-%m-%d")


def run(command: str, args: list[str], directory: Path = None, log_dir: Path = None,
        looked: bool = True) -> str:
    """One watch-list command with the arguments as the command parser split them. Returns ''
    when it ran and the reason when it could not — the error the LLM is told. `looked` is
    whether a save counts as a check of the item (the assistant's does, the curator's does not)."""
    args = [a.strip() for a in args]
    if command == "save_watch":
        if len(args) < 2 or not args[1]:
            return "save_watch failed: expected 'name, note, due, every' parameters"
        if save(args[0], args[1], args[2] if len(args) > 2 else None, directory, log_dir,
                every=args[3] if len(args) > 3 else None, looked=looked) is None:
            return "save_watch failed: the name must contain letters or digits"
        return ""
    if command == "checked_watch":
        if not args or not args[0]:
            return "checked_watch failed: expected the item's name"
        if check(args[0], directory) is None:
            open_names = ", ".join(item.name for item in items(directory)) or "none"
            return f"checked_watch failed: no item named '{slug(args[0]) or args[0]}'. Open: {open_names}"
        return ""
    if command == "delete_watch":
        if not args or not args[0]:
            return "delete_watch failed: expected the item's name"
        if delete(args[0], ", ".join(args[1:]), directory, log_dir) is None:
            open_names = ", ".join(item.name for item in items(directory)) or "none"
            return f"delete_watch failed: no item named '{slug(args[0]) or args[0]}'. Open: {open_names}"
        return ""
    return f"{command} is not a watch-list command"
