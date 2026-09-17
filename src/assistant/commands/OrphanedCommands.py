"""Outcomes of background commands that outlived their killed request.

A new user message supersedes the running request (Assistant.kill_all), but an
in-flight background command — a slow browse_act, a page fetch — keeps running on
its worker thread. Its reply used to be dropped on the floor, so the next request's
LLM had no idea whether the action ran, failed, or was still going, and would invent
an explanation (the 2026-08-31 "server restarted mid-checkout" confabulation).

Killed CommandProcessors register their in-flight command here; when the command
eventually publishes, the entry is resolved with the outcome. The next request
drains the registry into a transient system history note so the model can tell the
user what actually happened.
"""
import threading
import time

from common.logging import info, trace

_MAX_ENTRIES = 8
_ENTRY_TTL = 15 * 60  # forget outcomes nobody asked about within 15 minutes
_OUTCOME_MAX_CHARS = 600


class OrphanEntry:
    def __init__(self, description: str, outcome: str | None = None, is_error: bool = False):
        self.description = description
        self.outcome = outcome
        self.outcome_is_error = is_error
        self.created_at = time.time()
        self.reported_pending = False  # "still running" was already shown once


class OrphanedCommandRegistry:
    """Process-wide; thread-safe (kill paths, bg command workers and request threads)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: list[OrphanEntry] = []

    def register_pending(self, description: str) -> OrphanEntry:
        """A killed request left this command running; outcome unknown for now."""
        entry = OrphanEntry(description)
        with self._lock:
            self._add(entry)
        info(f"Orphaned command registered (still running): {description}")
        return entry

    def register_completed(self, description: str, outcome: str, is_error: bool):
        """A killed request had this command's reply ready but never consumed it."""
        entry = OrphanEntry(description, self._clip(outcome), is_error)
        with self._lock:
            self._add(entry)
        info(f"Orphaned command registered (completed, unconsumed): {description}")

    def resolve(self, entry: OrphanEntry, outcome: str, is_error: bool):
        """The command a killed request left running has finished."""
        with self._lock:
            entry.outcome = self._clip(outcome)
            entry.outcome_is_error = is_error
        info(f"Orphaned command resolved: {entry.description} "
             f"({'error' if is_error else 'ok'}, {len(entry.outcome)} chars)")

    def drain_note(self) -> str | None:
        """Render what the next request's LLM should know, or None when there is nothing.

        Resolved entries are reported once and removed. Pending entries are reported
        once as still-running and kept until they resolve or expire — the resolution
        then gets its own report on the request after that.
        """
        now = time.time()
        lines = []
        with self._lock:
            self._entries = [e for e in self._entries if now - e.created_at < _ENTRY_TTL]
            for entry in list(self._entries):
                if entry.outcome is not None:
                    lines.append(
                        f"Your command {entry.description} from an earlier interrupted request "
                        f"finished after the interruption. Its result (never shown to you until "
                        f"now):\n{entry.outcome}")
                    self._entries.remove(entry)
                elif not entry.reported_pending:
                    lines.append(
                        f"Your command {entry.description} from an earlier interrupted request "
                        f"is still running; its outcome is not known yet.")
                    entry.reported_pending = True
        if not lines:
            trace("No orphaned command outcomes to report.")
            return None
        return "\n\n".join(lines)

    def _add(self, entry: OrphanEntry):
        self._entries.append(entry)
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]

    @staticmethod
    def _clip(outcome: str) -> str:
        outcome = (outcome or "").strip() or "(empty result)"
        if len(outcome) > _OUTCOME_MAX_CHARS:
            outcome = outcome[:_OUTCOME_MAX_CHARS] + " …[truncated]"
        return outcome


_registry = OrphanedCommandRegistry()


def orphan_registry() -> OrphanedCommandRegistry:
    return _registry
