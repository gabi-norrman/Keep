"""memory/watchlist/ — everything the assistant is keeping an eye on, one small file per open item.

An item is something with a state that will change or end: an order on its way, an invoice to
pay, an appointment, an open decision, a reminder. It answers to its name, like a procedure
does. Its note is the item's *current* state and is replaced when the item is saved again, so
an item lives in one place instead of as a trail of remembered sentences. Deleting an item
removes its file; every replaced or deleted version is copied to `log/memory/watchlist/`
first, which is a debugging trail the LLM never reads.

The `save_watch` and `delete_watch` commands and the memory curator's command block both go
through `run`; the prompt's watch-list section renders `render`.
"""
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from common.logging import info, trace
from common.Paths import MEMORY_DIR, MEMORY_LOG_DIR

WATCHLIST_DIR = MEMORY_DIR / "watchlist"
LOG_DIR = MEMORY_LOG_DIR / "watchlist"
COMMANDS = ("save_watch", "delete_watch")
# An item nobody has touched this long, or whose due date passed this long ago, is marked
# STALE in the prompt: the assistant and the curator then know which ones to look at.
STALE_AFTER_DAYS = 14
OVERDUE_AFTER_DAYS = 2
EMPTY = "Nothing is being watched."

lock = threading.RLock()


@dataclass
class Item:
    name: str
    note: str = ""
    due: str = ""
    created: str = ""
    updated: str = ""

    def text(self, closing: str = "") -> str:
        header = [f"# {self.name}", f"Due: {self.due or '-'}", f"Created: {self.created}",
                  f"Updated: {self.updated}"]
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
    fields = {"due": "", "created": "", "updated": ""}
    for line in header.splitlines():
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            if key.lower() in fields:
                fields[key.lower()] = value.strip()
    if fields["due"] == "-":
        fields["due"] = ""
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


def save(name: str, note: str, due: str | None = None, directory: Path = None, log_dir: Path = None) -> Item | None:
    """Start watching `name`, or replace the note of the item that already answers to it. The
    due date is kept unless one is given ('-' clears it). None when the name has no letters or digits."""
    directory = directory or WATCHLIST_DIR
    if not slug(name):
        return None
    today = date.today().isoformat()
    due = None if due is None or not due.strip() else due.strip()
    with lock:
        item = get(name, directory)
        if item is None:
            item = Item(slug(name), note.strip(), "" if due in (None, "-") else due, today, today)
            directory.mkdir(parents=True, exist_ok=True)
            info(f"Watch list: '{item.name}' added" + (f", due {item.due}" if item.due else ""))
        else:
            _log_copy(item, log_dir or LOG_DIR)
            item.note = note.strip()
            if due is not None:
                item.due = "" if due == "-" else due
            item.updated = today
            info(f"Watch list: '{item.name}' updated")
        _path(name, directory).write_text(item.text(), encoding="utf-8")
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


def render(directory: Path = None, today: date = None) -> str:
    """The open items as the prompt and the curator show them."""
    open_items = items(directory)
    if not open_items:
        return EMPTY
    blocks = []
    for item in open_items:
        facts = ([f"due {item.due}"] if item.due else []) + [f"updated {item.updated}"]
        if stale := staleness(item, today):
            facts.append(stale)
        blocks.append(f"`{item.name}` ({', '.join(facts)})" + (f"\n{item.note}" if item.note else ""))
    return "\n\n".join(blocks)


def version(directory: Path = None) -> tuple:
    """Changes whenever the rendered list would: on any save or delete, and at midnight
    (staleness is counted in days)."""
    directory = directory or WATCHLIST_DIR
    try:
        files = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in directory.glob("*.md")))
    except OSError:
        files = ()
    return files, time.strftime("%Y-%m-%d")


def run(command: str, args: list[str], directory: Path = None, log_dir: Path = None) -> str:
    """One watch-list command with the arguments as the command parser split them. Returns ''
    when it ran and the reason when it could not — the error the LLM is told."""
    args = [a.strip() for a in args]
    if command == "save_watch":
        if len(args) < 2 or not args[1]:
            return "save_watch failed: expected 'name, note, due' parameters"
        if save(args[0], args[1], args[2] if len(args) > 2 else None, directory, log_dir) is None:
            return "save_watch failed: the name must contain letters or digits"
        return ""
    if command == "delete_watch":
        if not args or not args[0]:
            return "delete_watch failed: expected the item's name"
        if delete(args[0], ", ".join(args[1:]), directory, log_dir) is None:
            open_names = ", ".join(item.name for item in items(directory)) or "none"
            return f"delete_watch failed: no item named '{slug(args[0]) or args[0]}'. Open: {open_names}"
        return ""
    return f"{command} is not a watch-list command"
