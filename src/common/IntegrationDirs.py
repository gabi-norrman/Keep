"""Where the integration folders are: `src/integrations/` and the directories config.yaml's
`extra_integration_dirs` lists.

An install's own integrations — a local API, a hardware bridge, a workflow nobody else has —
can live outside the checkout, so a pull never touches them and nothing that runs in the
checkout (the unit suite, the nightly Claude Code runs) sees them. A listed directory is laid
out like `src/integrations/`: it holds integration folders, each with the same pieces
(`<Name>Integration.py`, `<Name>Commands.py`, `toolkit.md`, `config.example.yaml`,
`requirements.txt` …). The directories join the `integrations` package's `__path__`
(integrations/__init__.py), so `~/KeepLocal/schoolsoft/` is imported as `integrations.schoolsoft`
and every module discovery — integration classes, command sets, section generators, console
tabs — finds it as it finds a folder of the repository; the file globs (toolkits, config
fragments, requirements) go through `files()`.

A folder named like one before it (the repository's first, then the directories in the order
listed) is shadowed — Python imports the first of the name — so it is logged and left out
everywhere. A toolkit, command or config section a local folder shares with another is refused
by the same checks that guard the repository's folders.
"""
import os
from functools import lru_cache
from pathlib import Path

from common.Paths import AISERVER_DIR, INTEGRATIONS_DIR
from common.logging import error, info, trace

EXTRA_DIRS_KEY = "extra_integration_dirs"


@lru_cache(maxsize=1)
def extra_roots() -> tuple[Path, ...]:
    """The directories `extra_integration_dirs` lists, resolved (`~` expanded, a relative path
    taken from the repository root). One that is missing, the repository's own, or itself an
    integration folder is logged and left out."""
    from common.ConfigManager import config
    listed = config.get(EXTRA_DIRS_KEY, []) or []     # an install's config.yaml may predate the key
    if isinstance(listed, str):
        listed = [listed]
    if not isinstance(listed, list):
        error(f"IntegrationDirs: {EXTRA_DIRS_KEY} must be a list of directories, not {type(listed).__name__}; ignored")
        return ()
    own = INTEGRATIONS_DIR.resolve()
    roots: list[Path] = []
    for entry in listed:
        path = Path(os.path.expanduser(str(entry)))
        path = (path if path.is_absolute() else AISERVER_DIR / path).resolve()
        if not path.is_dir():
            error(f"IntegrationDirs: {entry} is not a directory; left out")
        elif path == own or path in roots:
            trace(f"IntegrationDirs: {entry} is already an integration directory; listed once")
        elif (path / "__init__.py").exists():
            error(f"IntegrationDirs: {entry} is itself an integration folder (it holds __init__.py) — "
                  f"list the directory that holds it ({path.parent}); left out")
        else:
            roots.append(path)
    if roots:
        info(f"IntegrationDirs: local integration directories {', '.join(map(str, roots))}")
    return tuple(roots)


def roots() -> tuple[Path, ...]:
    """Every directory of integration folders, the repository's first."""
    return (INTEGRATIONS_DIR, *extra_roots())


@lru_cache(maxsize=1)
def _folders() -> tuple[tuple[Path, bool], ...]:
    """(folder, is local) for every integration folder, in `roots()` order and by name within a
    root; a folder whose name an earlier one has is logged and left out."""
    seen: dict[str, Path] = {}
    out: list[tuple[Path, bool]] = []
    for root in roots():
        local = root != INTEGRATIONS_DIR
        for path in sorted(p for p in root.iterdir() if (p / "__init__.py").is_file()):
            if path.name in seen:
                error(f"IntegrationDirs: {path} is shadowed by {seen[path.name]} (same folder name); left out")
                continue
            seen[path.name] = path
            out.append((path, local))
    local = [p.name for p, is_local in out if is_local]
    if local:
        info(f"IntegrationDirs: local integration folders {', '.join(local)}")
    return tuple(out)


def local_folder(folder: str) -> Path | None:
    """The path of the local integration folder called *folder* (`schoolsoft` →
    `~/KeepLocal/schoolsoft`), None when the name is one of the repository's own or no folder
    has it — what tells a job on an install's own folders from a PR on the repository's."""
    for path, local in _folders():
        if path.name == folder:
            return path if local else None
    return None


def files(name: str, local_only: bool = False) -> list[Path]:
    """`<folder>/<name>` of every integration folder that has one (`toolkit.md`,
    `config.example.yaml`, `requirements.txt`), the repository's first; with *local_only*,
    those of the extra directories alone."""
    return [folder / name for folder, local in _folders()
            if (local or not local_only) and (folder / name).is_file()]


def reset():
    """Forget the resolved directories (tests change the config)."""
    extra_roots.cache_clear()
    _folders.cache_clear()
