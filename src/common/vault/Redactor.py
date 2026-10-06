"""Swap secrets for `{{secret:<id>}}` placeholders on the way to an LLM and back on the
way out (VAULT_PLAN §3.4), plus `RestoringStream`, which does the same on a chunk stream
without ever emitting half a placeholder or half a value (§3.5).

Everything derived from the vault (the combined pattern, the group→id table, the id→secret
table) lives in one immutable `_Built` snapshot per vault version. A call takes the snapshot
once and works from it, so a TUI edit that rebuilds mid-request can never pair one version's
group names with another's ids. With an empty (or locked) vault the redactor is an identity
and returns its input untouched.
"""
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Iterator

from common.logging import info, fine
from common.vault.SecretKinds import pattern
from common.vault.Vault import Vault, Secret

PLACEHOLDER_PREFIX = "{{secret:"
PLACEHOLDER_RE = re.compile(r"\{\{\s*secret\s*:\s*([A-Za-z0-9_\- ]+?)\s*\}\}")


def placeholder(secret_id: str) -> str:
    return f"{PLACEHOLDER_PREFIX}{secret_id}}}}}"


def normalize_id(raw: str) -> str:
    """Lenient id forms the LLM produces: `Alex_Mobile`, `alex mobile` → `alex-mobile`."""
    return re.sub(r"[\s_]+", "-", raw.strip().lower())


_storage_redactor: "Redactor | None" = None
_storage_lock = threading.Lock()


def redact_for_storage(text: str) -> str:
    """`text` with every vault value swapped back for its placeholder — for what the
    assistant writes to disk out of an LLM reply (`remember`, the watch list, a
    procedure, the curator's rewritten bank) or reports in the beta telemetry. `VaultLLM` restores placeholders across
    the whole reply so a browse_act can type the real card, which means a
    `save_procedure` in the same reply arrives with the real card in it too; the LLM
    only ever sees the placeholder (the next load is redacted again), the file is
    where the value would leak. Identity when the vault is off or empty."""
    global _storage_redactor
    try:
        vault = Vault.get_instance()
        with _storage_lock:
            if _storage_redactor is None or _storage_redactor.vault is not vault:
                _storage_redactor = Redactor(vault)
            redactor = _storage_redactor
        redacted, count = redactor.redact(text)
    except Exception as e:  # a vault that cannot be opened must not block a memory write
        info(f"Vault: storage redaction skipped ({type(e).__name__}: {e})")
        return text
    if count:
        info(f"Vault: {count} value(s) swapped for placeholders before the text is stored or reported")
    return redacted


@dataclass(frozen=True)
class _Built:
    """What one vault version compiles to. Published whole and never mutated."""
    version: int
    pattern: re.Pattern | None            # None: no secrets, the redactor is an identity
    masked_pattern: re.Pattern | None
    group_ids: dict[str, str]             # pattern group name → secret id
    by_id: dict[str, Secret]

    def secret_for(self, match: re.Match) -> Secret:
        return self.by_id[self.group_ids[match.lastgroup]]


class Redactor:
    def __init__(self, vault: Vault):
        self.vault = vault
        self._built: _Built | None = None
        self._build_lock = threading.Lock()

    @property
    def active(self) -> bool:
        """True when the vault has entries, i.e. redaction actually does something."""
        return self._ensure_built().pattern is not None

    def redact(self, text: str) -> tuple[str, int]:
        built = self._ensure_built()
        if built.pattern is None:
            self._log_skipped()
            return text, 0
        counts: Counter = Counter()
        redacted = self._redact(built, text, counts)
        self._log_redacted(counts)
        return redacted, sum(counts.values())

    def redact_messages(self, messages: list[dict]) -> list[dict]:
        """Copy of `messages` with every `str` content and every `text` block redacted;
        image blocks (and any other block type) are shared, not copied."""
        built = self._ensure_built()
        if built.pattern is None:
            self._log_skipped()
            return messages
        counts: Counter = Counter()
        redacted = []
        for message in messages:
            copy = dict(message)
            copy["content"] = self._redact_content(built, message.get("content"), counts)
            redacted.append(copy)
        self._log_redacted(counts)
        return redacted

    def spans(self, text: str) -> list[tuple[int, int]]:
        """(start, end) of every secret occurrence in `text` — exactly what `redact` would
        swap — for a UI that marks them instead. Not logged: it is called per drawn frame."""
        built = self._ensure_built()
        if built.pattern is None:
            return []
        return [match.span() for match in built.pattern.finditer(text)]

    def mask(self, text: str) -> str:
        """`text` with every secret occurrence swapped for its `Secret.masked` form (`•••• 1234`,
        `a•••@example.com`) — for a UI that must never show a value in the clear. Not logged: it
        runs whenever such a UI reloads its text."""
        built = self._ensure_built()
        if built.pattern is None:
            return text
        return built.pattern.sub(lambda m: built.secret_for(m).masked, text)

    def masked_spans(self, text: str) -> list[tuple[int, int]]:
        """(start, end) of every masked form in `text` — the pieces `mask` swapped in — for a
        UI that marks them. Not logged: it is called per drawn frame."""
        built = self._ensure_built()
        if built.masked_pattern is None:
            return []
        return [match.span() for match in built.masked_pattern.finditer(text)]

    def restore(self, text: str) -> tuple[str, int]:
        """Placeholders back to real values. An unknown id stays visible (and is logged) so
        an invented placeholder never silently disappears."""
        if "{{" not in text:
            return text, 0
        built = self._ensure_built()
        restored, unknown = [], []

        def swap(match: re.Match) -> str:
            secret = built.by_id.get(normalize_id(match.group(1)))
            if secret is None:
                unknown.append(match.group(0))
                return match.group(0)
            restored.append(secret.id)
            return secret.value

        result = PLACEHOLDER_RE.sub(swap, text)
        for ph in unknown:
            info(f"Vault: unknown placeholder {ph} left visible")
        if restored:
            fine("Vault: restored " + ", ".join(placeholder(i) for i in restored))
        return result, len(restored)

    def _ensure_built(self) -> _Built:
        """The snapshot for the vault's current version, built once (under the lock) when the
        version moved. Callers work from the returned object, never from `self._built`."""
        built = self._built
        version = self.vault.version
        if built is not None and built.version == version:
            return built
        with self._build_lock:
            built = self._built
            if built is not None and built.version == version:
                return built
            built = self._build(version)
            self._built = built
        return built

    def _build(self, version: int) -> _Built:
        # `version` was read before the secrets: an edit landing in between bumps it again, so
        # the next call rebuilds instead of trusting a snapshot labelled with the old number.
        # Longest canonical first, so a value that contains another value wins.
        secrets = sorted(self.vault.secrets(), key=lambda s: len(s.canonical), reverse=True)
        group_ids = {f"g{i}": s.id for i, s in enumerate(secrets)}
        alternatives = [f"(?P<g{i}>{pattern(s.kind, s.canonical, self.vault.country_code)})"
                        for i, s in enumerate(secrets)]
        # Longest mask first too: `a•••@example.com` must win over a bare `••••••••`
        masks = sorted({s.masked for s in secrets}, key=len, reverse=True)
        if secrets:
            fine(f"Vault: redaction pattern built for {len(secrets)} secret(s) (vault version {version})")
        return _Built(version=version,
                      pattern=re.compile("|".join(alternatives)) if alternatives else None,
                      masked_pattern=re.compile("|".join(map(re.escape, masks))) if masks else None,
                      group_ids=group_ids,
                      by_id={s.id: s for s in secrets})

    @staticmethod
    def _redact(built: _Built, text: str, counts: Counter) -> str:
        def swap(match: re.Match) -> str:
            secret_id = built.group_ids[match.lastgroup]
            counts[secret_id] += 1
            return placeholder(secret_id)

        return built.pattern.sub(swap, text)

    def _redact_content(self, built: _Built, content, counts: Counter):
        if isinstance(content, str):
            return self._redact(built, content, counts)
        if isinstance(content, list):
            return [{**block, "text": self._redact(built, block["text"], counts)}
                    if isinstance(block, dict) and block.get("type") == "text" else block
                    for block in content]
        return content

    def _log_skipped(self):
        # A locked vault (wrong key, corrupt file) passes everything through unredacted —
        # that must be visible in floxy.out. An empty vault is the normal state and stays silent.
        if self.vault.locked:
            info(f"Vault: LOCKED ({self.vault.error}) — nothing redacted")

    @staticmethod
    def _log_redacted(counts: Counter):
        if not counts:
            fine("Vault: nothing to redact")
            return
        summary = ", ".join(f"{sid} ×{n}" if n > 1 else sid for sid, n in counts.items())
        fine(f"Vault: redacted {sum(counts.values())} secret(s) [{summary}]")


# The head of a placeholder in the padded spelling PLACEHOLDER_RE accepts — `{{ secret : id }}` —
# and every proper prefix of it. A chunk ending in one of those may still turn into a placeholder.
_OPEN_HEAD_RE = re.compile(r"\{\{[ \t]*secret[ \t]*:")
_PARTIAL_HEAD_RE = re.compile(r"\{(?:\{(?:[ \t]*(?:s(?:e(?:c(?:r(?:e(?:t(?:[ \t]*:?)?)?)?)?)?)?)?)?)?\Z")


class RestoringStream:
    """Iterates an LLM chunk stream, restoring placeholders whole.

    Text is passed through as soon as it arrives; the only thing held back is a tail that
    could still become a placeholder — a proper prefix of `{{secret:` (`{`, `{{sec`, or the
    padded `{{ sec` that `restore` also accepts) or an open `{{secret:…` without `}}`. An open
    placeholder is released once it spans 80 characters or a newline: it was not one.
    Whatever is pending at end of stream is flushed.
    """
    HOLD_LIMIT = 80

    def __init__(self, inner: Iterable[str], redactor: Redactor):
        self.inner = inner
        self.redactor = redactor

    def __iter__(self) -> Iterator[str]:
        pending = ""
        for chunk in self.inner:
            pending += chunk
            cut = self.hold_from(pending)
            safe, pending = pending[:cut], pending[cut:]
            if safe:
                yield self.redactor.restore(safe)[0]
        if pending:
            yield self.redactor.restore(pending)[0]

    @classmethod
    def hold_from(cls, pending: str) -> int:
        """Index from which `pending` must be held back; len(pending) when nothing qualifies."""
        for i in range(max(0, len(pending) - cls.HOLD_LIMIT), len(pending)):
            if pending[i] != "{":
                continue
            tail = pending[i:]
            if _OPEN_HEAD_RE.match(tail):
                if "}}" not in tail and "\n" not in tail and len(tail) < cls.HOLD_LIMIT:
                    return i
            elif _PARTIAL_HEAD_RE.match(tail):
                return i
        return len(pending)
