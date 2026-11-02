"""What settings exist, read from config.example.yaml — never from config.yaml.

The example is the schema the assistant reasons about: its sections, the leaves under each,
their types, and the how-to comments ("Generate at https://…"). Sections the example ships
commented out (`#photos:`, `#ai:` …) count too; their text is returned with the leading `#`
stripped so the reader sees real YAML. plans/SELF_SETUP_PLAN.md §3.1.

The example is one text made of several files: the root `config.example.yaml` and, for an
integration's own section, the fragment in its folder (`src/integrations/<name>/config.example.yaml`).
A pointer line in the root (`# >> tesla: src/integrations/tesla/config.example.yaml`) is replaced by
the fragment it names, so the section stands where it always did; a fragment nothing points at
(a folder added later) follows after the root, by folder name, and after those the fragments of
the install's local integration folders (`extra_integration_dirs`, common/IntegrationDirs.py).
Everything below reads that one text and knows nothing of the files.
"""
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from common import IntegrationDirs
from common.Paths import AISERVER_DIR, INTEGRATIONS_DIR
from common.logging import error, info, trace

EXAMPLE_PATH = AISERVER_DIR / "config.example.yaml"
FRAGMENT_NAME = "config.example.yaml"
# Where the fragments are, seen from the root example: tests that swap EXAMPLE_PATH for a file
# of their own get that file's surroundings, not this repository's folders.
_FRAGMENTS_FROM_ROOT = INTEGRATIONS_DIR.relative_to(AISERVER_DIR)
# The root's stand-in for a section that lives in a fragment: the section name (for the human
# reader) and the fragment's path from the root example's directory.
_POINTER_RE = re.compile(r"^# >> ([A-Za-z_][A-Za-z0-9_]*): (\S+)$")

# A top-level key at column 0, optionally commented out (`#photos:` — the example comments
# code out with `#` glued to the key, prose comments always have `# ` with a space).
_SECTION_RE = re.compile(r"^(#?)([A-Za-z_][A-Za-z0-9_]*):(\s|$)")
_PROSE_COMMENT_RE = re.compile(r"^#( |$)")
# Dotted keys, with list items addressed by index: icloud.devices[0].display_name. The
# parens form, devices(0), is accepted too — the command parser turns [ ] into ( ) for
# every command that is not on its keep-brackets list, and the LLM should not have to care.
_INDEX = r"(?:\[\d+\]|\(\d+\))"
_KEY_RE = re.compile(rf"^[a-z0-9_]+{_INDEX}?(\.[a-z0-9_]+{_INDEX}?)*$")
_SEGMENT_RE = re.compile(r"^([a-z0-9_]+)(?:[\[(](\d+)[\])])?$")


def split_key(key: str) -> list:
    """'icloud.devices[0].id' → ['icloud', 'devices', 0, 'id']."""
    parts: list = []
    for segment in key.split("."):
        m = _SEGMENT_RE.match(segment)
        if m is None:
            raise ValueError(f"malformed setting key '{key}'")
        parts.append(m.group(1))
        if m.group(2) is not None:
            parts.append(int(m.group(2)))
    return parts


def normalize_key(key: str) -> str:
    """The canonical spelling of a key — indexes as [N] — or *key* unchanged when it is
    not a well-formed key (the validators report that with a better message)."""
    key = key.strip()
    return join_key(split_key(key)) if _KEY_RE.match(key) else key


def join_key(parts: list) -> str:
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else part)
    return out


class _Section:
    def __init__(self, name: str, commented: bool, key_line: int):
        self.name = name
        self.commented = commented
        self.key_line = key_line
        self.start = key_line   # first attached leading comment line
        self.end = key_line     # last line of the block (inclusive)


def _section_names(lines: list[str]) -> list[str]:
    return [m.group(2) for m in map(_SECTION_RE.match, lines) if m]


def _read_fragment(path: Path) -> Optional[list[str]]:
    """The fragment's lines, or None (logged) when it cannot be read, is not YAML or holds no section."""
    try:
        text = path.read_text(encoding="utf-8")
        yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        error(f"ConfigSchema: fragment {path} left out — {type(e).__name__}: {e}")
        return None
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not _section_names(lines):
        error(f"ConfigSchema: fragment {path} left out — it holds no top-level section")
        return None
    return lines


@lru_cache(maxsize=1)
def _lines() -> list[str]:
    """The example as one text: the root file with every pointer replaced by its fragment,
    then the fragments no pointer names. A fragment that fails to load, or whose section
    another file already defines, is logged and left out — the rest of the schema stands."""
    root = EXAMPLE_PATH.parent
    root_lines = EXAMPLE_PATH.read_text().splitlines()
    owners: dict[str, Path] = {name: EXAMPLE_PATH for name in _section_names(root_lines)}

    def claim(path: Path) -> Optional[list[str]]:
        lines = _read_fragment(path)
        if lines is None:
            return None
        taken = [name for name in _section_names(lines) if name in owners]
        if taken:
            error(f"ConfigSchema: fragment {path} left out — section '{taken[0]}' is already defined in {owners[taken[0]]}")
            return None
        owners.update({name: path for name in _section_names(lines)})
        return lines

    out: list[str] = []
    pointed: set[Path] = set()
    for line in root_lines:
        m = _POINTER_RE.match(line)
        if m is None:
            out.append(line)
            continue
        path = root / m.group(2)
        pointed.add(path.resolve())
        out.extend(claim(path) or [])
    appended = 0
    local = IntegrationDirs.files(FRAGMENT_NAME, local_only=True)
    for path in sorted((root / _FRAGMENTS_FROM_ROOT).glob(f"*/{FRAGMENT_NAME}")) + local:
        if path.resolve() in pointed:
            continue
        fragment = claim(path)
        if fragment is not None:
            out.extend([""] + fragment)
            appended += 1
    if pointed or appended:
        trace(f"ConfigSchema: {EXAMPLE_PATH.name} read with {len(pointed)} pointed and {appended} appended fragment(s)")
    return out


@lru_cache(maxsize=1)
def _sections() -> dict[str, _Section]:
    lines = _lines()
    found: list[_Section] = []
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            found.append(_Section(m.group(2), commented=bool(m.group(1)), key_line=i))
    for s in found:
        j = s.key_line - 1
        while j >= 0 and _PROSE_COMMENT_RE.match(lines[j]):
            j -= 1
        s.start = j + 1
    for s, nxt in zip(found, found[1:] + [None]):
        end = (nxt.start if nxt else len(lines)) - 1
        while end > s.key_line and not lines[end].strip():
            end -= 1
        s.end = end
    result = {s.name: s for s in found}
    trace(f"ConfigSchema: {len(result)} sections in {EXAMPLE_PATH.name} "
          f"({sum(1 for s in found if s.commented)} commented out)")
    return result


def reset():
    """Forget the cached example (tests swap EXAMPLE_PATH)."""
    _lines.cache_clear()
    _sections.cache_clear()
    _schema.cache_clear()


def sections() -> list[str]:
    """Top-level section names in file order, commented-out ones included."""
    return list(_sections())


def _lookup(section: str) -> Optional[_Section]:
    wanted = section.strip().lower()
    for name, s in _sections().items():
        if name.lower() == wanted:
            return s
    return None


def section_text(section: str) -> Optional[str]:
    """The section's block of the example verbatim: the comments directly above it, the key
    and everything indented under it. A commented-out section comes back uncommented.
    None when there is no such section."""
    s = _lookup(section)
    if s is None:
        info(f"ConfigSchema: no section '{section}' in the example")
        return None
    lines = _lines()
    out = list(lines[s.start:s.key_line])
    for line in lines[s.key_line:s.end + 1]:
        out.append(line[1:] if s.commented and line.startswith("#") else line)
    return "\n".join(out)


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    """The example parsed, with every commented-out section uncommented and merged in."""
    data = yaml.safe_load("\n".join(_lines())) or {}
    for s in _sections().values():
        if not s.commented:
            continue
        code = "\n".join(line[1:] if line.startswith("#") else line for line in _lines()[s.key_line:s.end + 1])
        try:
            parsed = yaml.safe_load(code) or {}
        except yaml.YAMLError as e:
            info(f"ConfigSchema: commented-out section '{s.name}' does not parse, skipping its types: {e}")
            continue
        data.update(parsed)
    return data


def example_value(key: str) -> Optional[Any]:
    """The example's value at a dotted key, None when the example has no such key. Any list
    index reads the example's first item: the example shows one entry's shape, not N."""
    if not _KEY_RE.match(key):
        return None
    node: Any = _schema()
    for part in split_key(key):
        if isinstance(part, int):
            if not isinstance(node, list) or not node:
                return None
            node = node[0]
            continue
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def is_scalar_list(key: str) -> bool:
    """The example holds a list of plain values (or an empty list) at *key* — enabled_integrations —
    as opposed to a list of sections such as icloud.devices."""
    value = example_value(key)
    return isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value)


def leaf_type(key: str) -> Optional[type]:
    """type of the example's value at *key* (bool/int/float/str/list/dict); None when the
    example does not list the key."""
    value = example_value(key)
    return None if value is None else type(value)


def leaves(key: str) -> list[str]:
    """Dotted keys of every non-dict value under *key* in the example (a list counts as one
    leaf). [key] itself when the example holds a scalar there, [] when it holds nothing."""
    value = example_value(key)
    if value is None:
        return []
    if not isinstance(value, dict):
        return [key]
    out: list[str] = []
    for name, child in value.items():
        out.extend(leaves(f"{key}.{name}"))
    return out


def is_known_key(key: str) -> bool:
    """A dotted key whose top-level segment is a section of the example. Keys the example
    does not spell out under a known section are allowed: the example is not exhaustive."""
    if not _KEY_RE.match(key):
        return False
    return _lookup(split_key(key)[0]) is not None
