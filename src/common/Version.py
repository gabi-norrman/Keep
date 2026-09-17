"""The running system's version, read from git: `vYYYY.MM.N` — the month of the last commit
and the number of commits behind HEAD, which only grows along master so it names one commit
(`git rev-list --reverse master | sed -n <N>p`). `detail()` adds what the short form cannot
tell: the hash (two side branches can share a count) and whether the tree has local edits."""
import subprocess
from functools import lru_cache
from typing import Optional

from common.Paths import REPO_DIR
from common.logging import info

UNKNOWN = "unknown"


def _git(*args: str) -> Optional[str]:
    try:
        result = subprocess.run(["git", *args], cwd=REPO_DIR, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        info(f"Version: git {args[0]} failed: {e}")
        return None
    if result.returncode != 0:
        info(f"Version: git {args[0]} exited {result.returncode}: {result.stderr.strip()}")
        return None
    return result.stdout.strip()


@lru_cache(maxsize=None)
def version() -> str:
    month = _git("log", "-1", "--format=%cd", "--date=format:%Y.%m")
    count = _git("rev-list", "--count", "HEAD")
    if not month or not count:
        info("Version: not a git checkout, the version is unknown")
        return UNKNOWN
    return f"v{month}.{count}"


@lru_cache(maxsize=None)
def detail() -> str:
    """`v2026.09.3868 (1b6c9ce7, dirty)` — for the log and /health."""
    commit = _git("rev-parse", "--short=8", "HEAD")
    if version() == UNKNOWN or not commit:
        return version()
    dirty = ", dirty" if _git("status", "--porcelain") else ""
    return f"{version()} ({commit}{dirty})"
