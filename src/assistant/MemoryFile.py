"""memory/bank.md — the long-term memory bank — and the one lock every writer of it shares.

The bank holds what is durably true of the household: people, preferences, relationships,
contacts, standing rules. How-tos live in `Procedures`, things being tracked in `WatchList`;
the three together are the `memory/` directory.

Two things write the file: the curator's full rewrite (a read, minutes of LLM time, then a
write) and the `remember` command's append. The lock serialises the append with the curator's
final read-merge-write, so a note taken while the curator was thinking is carried over instead
of overwritten; `write` replaces the file atomically so a reader (the prompt's memory section,
the console's Memory tab) never sees a half-written bank.
"""
import os
import shutil
import threading

from common.logging import info
from common.Paths import RES_DIR

MEMORY_FILE = os.path.join("memory", "bank.md")
# Where the bank lived before the memory got a directory of its own; `seed` moves it over.
LEGACY_MEMORY_FILE = "memory.md"
# What a fresh install starts from: a placeholder heading plus the concepts the assistant
# may teach the user over time. The first curation rewrites it into a real bank.
FIRST_RUN_STUB = RES_DIR / "first_run_memory.md"

lock = threading.RLock()


def _ensure_parent(path: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def seed(path: str = MEMORY_FILE, stub=FIRST_RUN_STUB, legacy: str = None) -> bool:
    """Copy the first-run stub into place when there is no memory bank yet. Runs at every
    start, so a wiped install (`//wipe`) comes back with the stub too. An install from before
    the `memory/` directory has its bank moved there first: `legacy` is where it lived, and
    only the real bank path looks for the real legacy file — seeding any other path never
    moves it. Returns whether it copied the stub."""
    if legacy is None and path == MEMORY_FILE:
        legacy = LEGACY_MEMORY_FILE
    with lock:
        if not os.path.exists(path) and legacy and os.path.exists(legacy):
            _ensure_parent(path)
            shutil.move(legacy, path)
            info(f"Memory bank moved from {legacy} to {path}")
        if os.path.exists(path):
            info(f"Memory bank {path} exists, nothing to seed.")
            return False
        if not os.path.exists(stub):
            info(f"No memory bank at {path} and no first-run stub at {stub}, starting with none.")
            return False
        _ensure_parent(path)
        shutil.copyfile(stub, path)
    info(f"No memory bank at {path}: seeded it from {stub}")
    return True


def read(path: str = MEMORY_FILE) -> str:
    with lock:
        if not os.path.exists(path):
            return ""
        with open(path, "r") as f:
            return f.read()


def append(text: str, path: str = MEMORY_FILE):
    with lock:
        _ensure_parent(path)
        with open(path, "a") as f:
            f.write("\n" + text.strip() + "\n")
    info(f"Appended {len(text)} chars to {path}")


def write(text: str, path: str = MEMORY_FILE):
    """Replace the whole file in one step (write a sibling temp file, then rename over)."""
    with lock:
        _ensure_parent(path)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
