"""The owner's own say over which Home Assistant entities Keep sees, one line per rule in a
file the assistant edits by command. An `exclude` rule leaves the matching entities out of
the house data, the name index and the event feed whatever Home Assistant exposes; an
`include` rule keeps them in whatever Home Assistant hides and whatever `exposed_only` says,
and wins over an exclude. A pattern is an entity id, a glob on one (`binary_sensor.hp_*`) or a
bare domain (`sensor`, the whole domain). Home Assistant's registry is never touched: an entity
the owner wants recorded there but not read here stays enabled (issue #141)."""
import fnmatch
import re
import threading
from pathlib import Path
from typing import Optional

from common.Paths import MEMORY_DIR
from common.logging import info, trace

EXCLUDE = "exclude"
INCLUDE = "include"
_PATTERN = re.compile(r"^[a-z0-9_*?\[\]!-]+\.[a-z0-9_*?\[\]!-]+$")
_HEADER = ("# Keep's view of Home Assistant, edited by the assistant's commands: `exclude <pattern>` leaves the\n"
           "# matching entities out of the house data and the event feed, `include <pattern>` shows them whatever\n"
           "# Home Assistant hides. A pattern is an entity id or a glob on one; a bare domain is the whole domain.\n")


class RuleError(Exception):
    """A pattern that cannot be a rule; the message is for the LLM."""


def normalize(pattern: str) -> str:
    """The pattern as a rule stores it: lower case, a bare domain widened to `domain.*`."""
    pattern = pattern.strip().strip("\"'`").strip().lower()
    if not pattern:
        raise RuleError("No pattern given — an entity id such as light.kitchen, a glob such as sensor.unifi_*, "
                        "or a domain such as sensor.")
    if "." not in pattern:
        pattern = f"{pattern}.*"
    if not _PATTERN.match(pattern):
        raise RuleError(f"'{pattern}' is not an entity id pattern — an id reads domain.object_id, letters, digits "
                        f"and underscores, with * and ? as wildcards.")
    return pattern


class EntityRules:
    """The rules, pattern → exclude / include, in the order they were made."""

    PATH = MEMORY_DIR / "home_assistant_entity_rules.txt"

    def __init__(self, path: Optional[Path] = None):
        self.path = path or self.PATH
        self._rules: dict[str, str] = {}
        self._lock = threading.Lock()

    def load(self) -> "EntityRules":
        """The rules the file holds; a missing file is no rules. A line the file should not
        hold is skipped and logged, never fatal."""
        rules: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                kind, _, pattern = line.partition(" ")
                try:
                    if kind not in (EXCLUDE, INCLUDE):
                        raise RuleError(f"'{kind}' is neither {EXCLUDE} nor {INCLUDE}")
                    rules[normalize(pattern)] = kind
                except RuleError as e:
                    info(f"Home Assistant rules: line '{line}' of {self.path.name} skipped — {e}")
        with self._lock:
            self._rules = rules
        trace(f"Home Assistant rules: {len(rules)} loaded from {self.path.name}"
              if rules else f"Home Assistant rules: none ({self.path.name} {'empty' if self.path.exists() else 'absent'})")
        return self

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            lines = [f"{kind} {pattern}" for pattern, kind in self._rules.items()]
        self.path.write_text(_HEADER + "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def set(self, pattern: str, kind: str) -> tuple[str, Optional[str]]:
        """Make *pattern* an exclude or include rule, replacing the rule it had; returns the
        stored pattern and the kind it had before (None for a new one). Saved at once."""
        pattern = normalize(pattern)
        with self._lock:
            before = self._rules.pop(pattern, None)
            self._rules[pattern] = kind
        self.save()
        info(f"Home Assistant rules: {kind} {pattern}" + (f" (was {before})" if before else ""))
        return pattern, before

    def clear(self, pattern: str) -> tuple[str, Optional[str]]:
        """Drop the rule of *pattern*; returns the stored pattern and the kind it had, None
        when there was none."""
        pattern = normalize(pattern)
        with self._lock:
            before = self._rules.pop(pattern, None)
        if before:
            self.save()
            info(f"Home Assistant rules: cleared {before} {pattern}")
        else:
            trace(f"Home Assistant rules: nothing to clear for {pattern}")
        return pattern, before

    def rules(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._rules.items())

    def verdict(self, entity_id: str) -> Optional[str]:
        """INCLUDE when an include rule matches the id, else EXCLUDE when an exclude rule
        does, else None — the entity goes by Home Assistant's own settings."""
        with self._lock:
            kinds = {kind for pattern, kind in self._rules.items() if fnmatch.fnmatchcase(entity_id, pattern)}
        if INCLUDE in kinds:
            return INCLUDE
        if EXCLUDE in kinds:
            return EXCLUDE
        return None

    def __len__(self):
        return len(self._rules)

    @staticmethod
    def matches(pattern: str, entity_ids) -> list[str]:
        return sorted(e for e in entity_ids if fnmatch.fnmatchcase(e, pattern))
