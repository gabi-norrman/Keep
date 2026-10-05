"""Shrinks rendered history rows whose full text no longer serves the LLM, before the window is trimmed.

A browse snapshot's element list is dead the moment the next snapshot lands — its indices
reset — yet at up to COMMAND_RESULT_CHARS a row it was three quarters of what the window
held during a checkout: the window trimmed four times in nine minutes and the user's own
instructions fell out of it while the dead lists stayed (2026-10-05). The compressor
collapses every snapshot but the newest to its header, so the window holds the conversation.

The one rule every compression must obey: the result is a pure function of the rows it is
given. The trim cutoff is sticky so the rendered prefix stays byte-stable between requests
and the KV / prompt cache keeps hitting; a rule that looked at the clock or at anything
outside the rows would change bytes with no new row and silently defeat that. Supersession
is safe: a row renders differently exactly once, when the row that supersedes it lands at
the tail. The rows themselves (floxy.db, the TUI's history pane) are never changed.
"""
from common.logging import info, trace
from ..tools import SnapshotFormat

Rendered = list[tuple[dict, str, str]]   # MessageHistory's (entry, role, content), oldest first


class HistoryCompressor:
    def __init__(self):
        self._last_report: tuple[int, int] | None = None

    def compress(self, rendered: Rendered) -> Rendered:
        """The same rows with every superseded snapshot's element list collapsed."""
        from .MessageHistory import MessageHistory
        spans = []   # (row index, (marker line, end line)) of every snapshot element list
        for i, (entry, _, content) in enumerate(rendered):
            if entry["role"] != MessageHistory.ROLE_COMMAND:
                continue
            spans.extend((i, span) for span in self._snapshot_spans(content))
        if len(spans) <= 1:
            self._report(0, 0)
            return rendered

        out = list(rendered)
        saved = 0
        for i, (marker, end) in reversed(spans[:-1]):   # newest first keeps the line numbers valid
            entry, role, content = out[i]
            collapsed = self._collapse(content, marker, end)
            saved += len(content.encode("utf-8")) - len(collapsed.encode("utf-8"))
            out[i] = (entry, role, collapsed)
        self._report(len(spans) - 1, saved)
        return out

    def _report(self, collapsed: int, saved: int):
        report = (collapsed, saved)
        if not collapsed:
            trace("HistoryCompressor: nothing to compress (no superseded snapshot in the history)")
        elif report != self._last_report:
            info(f"HistoryCompressor: collapsed {collapsed} superseded snapshot(s), saving {saved / 1024:.1f}KB")
        else:
            trace(f"HistoryCompressor: {collapsed} superseded snapshot(s) collapsed, {saved / 1024:.1f}KB saved (unchanged)")
        self._last_report = report

    @staticmethod
    def _snapshot_spans(content: str) -> list[tuple[int, int]]:
        """(marker line, end line) of each element list in `content`, in order. A round that
        batched several commands joins their results with a blank line, so the list ends at
        the first line that is neither blank nor one of its own; a trailing blank line is
        the batch separator and stays."""
        lines = content.split("\n")
        spans = []
        j = 0
        while j < len(lines):
            if lines[j] != SnapshotFormat.ELEMENTS_MARKER:
                j += 1
                continue
            end = j + 1
            k = j + 1
            while k < len(lines) and (lines[k] == "" or SnapshotFormat.is_element_list_line(lines[k])):
                if lines[k]:
                    end = k + 1
                k += 1
            spans.append((j, end))
            j = end
        return spans

    @staticmethod
    def _collapse(content: str, marker: int, end: int) -> str:
        """`content` with the element list at [marker, end) replaced by a one-line note and
        the header's `Text:` digest dropped. The NOTE lines, URL, title, headings and alerts
        stay: they are what still tells the story once the page is gone."""
        lines = content.split("\n")
        body = lines[marker + 1:end]
        if body == [SnapshotFormat.NO_ELEMENTS]:
            stub = "(no interactive elements; a later snapshot replaced this one)"
        else:
            indices = [int(m) for line in body for m in SnapshotFormat.ELEMENT_INDEX.findall(line)]
            count = max(indices) + 1 if indices else 0
            stub = (f"({count} interactive elements omitted: a later snapshot replaced this one, "
                    f"its indices are stale)")
        head = lines[:marker]
        for i in range(len(head) - 1, -1, -1):
            if head[i].startswith(SnapshotFormat.TEXT_PREFIX):
                del head[i]
                break
            if head[i].startswith(SnapshotFormat.URL_PREFIX):
                break
        return "\n".join(head + [stub] + lines[end:])
