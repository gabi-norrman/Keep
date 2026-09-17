import importlib.metadata
import re
import sys
from pathlib import Path

from common.logging import error, info, trace
from common.Paths import AISERVER_DIR

REQUIREMENTS_FILE = AISERVER_DIR / "requirements.txt"
VOICE_REQUIREMENTS_FILE = AISERVER_DIR / "requirements-voice.txt"

REQUIRED_PYTHON = (3, 12)

_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?\s*==\s*(\S+)\s*$")
_DIRECT_REF_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*@\s*(\S+)\s*$")
_ARCHIVE_EXTENSIONS = (".whl", ".tar.gz", ".tar.bz2", ".zip")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_from_direct_ref(name: str, url: str) -> str | None:
    """Extract the pinned version from a direct-reference URL's archive name,
    e.g. .../en_core_web_sm-3.8.0-py3-none-any.whl -> '3.8.0'."""
    basename = url.rsplit("/", 1)[-1]
    for ext in _ARCHIVE_EXTENSIONS:
        if basename.endswith(ext):
            basename = basename[: -len(ext)]
            break
    else:
        return None
    m = re.match(r"([A-Za-z0-9_.\-]+?)-([0-9][A-Za-z0-9.!+]*)(?:-|$)", basename)
    if not m or _normalize(m.group(1)) != _normalize(name):
        return None
    return m.group(2)


class RequirementsChecker:
    """Verifies at startup that the Python version and pinned requirements match what is installed.

    Both groups are checked whatever the config enables: the assistant can switch the voice
    interface on from the chat (config_set "interfaces.voice.enabled"), and the restart that
    follows must not be the first time the install learns it lacks requirements-voice.txt.
    Any missing or mismatched package aborts the server with install instructions.
    """

    @staticmethod
    def verify_or_exit():
        RequirementsChecker._check_python_version()
        groups = [("base", REQUIREMENTS_FILE), ("voice", VOICE_REQUIREMENTS_FILE)]

        missing: list[str] = []
        mismatched: list[tuple[str, str, str]] = []
        checked = 0
        for group, path in groups:
            if not path.exists():
                error(f"{path} not found; skipping {group} requirements check.")
                continue
            g_missing, g_mismatched, g_checked = RequirementsChecker._check_file(path)
            missing += g_missing
            mismatched += g_mismatched
            checked += g_checked

        if missing or mismatched:
            RequirementsChecker._fail(missing, mismatched, [p.name for _, p in groups])
            return

        trace(f"All {checked} pinned packages match installed versions "
              f"({', '.join(g for g, _ in groups)}).")

    @staticmethod
    def _check_file(path: Path) -> tuple[list[str], list[tuple[str, str, str]], int]:
        missing: list[str] = []
        mismatched: list[tuple[str, str, str]] = []
        skipped: list[str] = []
        checked = 0

        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("-r "):
                    continue
                m = _REQ_LINE_RE.match(line)
                if m:
                    name, expected = m.group(1), m.group(2)
                else:
                    d = _DIRECT_REF_RE.match(line)
                    if not d:
                        skipped.append(line)
                        continue
                    name, url = d.group(1), d.group(2)
                    expected = _version_from_direct_ref(name, url)
                    if expected is None:
                        trace(f"Direct reference for {name}: no version in URL, checking presence only")
                try:
                    actual = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    missing.append(name)
                    continue
                if expected is not None and actual != expected:
                    mismatched.append((name, expected, actual))
                checked += 1

        for s in skipped:
            trace(f"Skipped unpinned/unparseable line: {s!r}")
        return missing, mismatched, checked

    @staticmethod
    def _check_python_version():
        actual = sys.version_info[:2]
        ver = sys.version.replace('\n', ' ')
        if actual == REQUIRED_PYTHON:
            trace(f"Python version: {ver}")
            return
        required_str = ".".join(str(x) for x in REQUIRED_PYTHON)
        bar = "=" * 70
        error("")
        error(bar)
        error("  PYTHON VERSION CHECK FAILED — server will not start")
        error(bar)
        error(f"  Required: Python {required_str}.x")
        error(f"  Found:    {ver}")
        error(bar)
        error(f"  Activate the {required_str} venv or rebuild it with python{required_str}.")
        error(bar)
        error("")
        sys.exit(1)

    @staticmethod
    def _fail(missing: list[str], mismatched: list[tuple[str, str, str]], files: list[str]):
        bar = "=" * 70
        error("")
        error(bar)
        error("  DEPENDENCY CHECK FAILED — server will not start")
        error(bar)
        if missing:
            error(f"  Missing packages ({len(missing)}):")
            for name in missing:
                error(f"    - {name}")
        if mismatched:
            error(f"  Version mismatches ({len(mismatched)}):")
            for name, expected, actual in mismatched:
                error(f"    - {name}: expected {expected}, installed {actual}")
        error(bar)
        error(f"  Fix with: pip install -r {files[-1]}")
        error(bar)
        error("")
        sys.exit(1)
