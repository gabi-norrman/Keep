"""Explains a prompt-cache miss: the first line of the prompt that differs from the previous request's, as a diff, beside where the server stopped reading.

Every backend hands the tracker the messages it sent and the server's own count of the
prompt tokens it read back from its cache (`AbstractLLM.report_cache_usage`). The tracker
keeps the previous request's prompt as lines and a break is when the cache read back less
than THRESHOLD percent of the previous prompt — a flat share of the current prompt misfires on
an ordinary follow-up round, whose new command result is uncached by rights.

A prefix cache can only stop at the first difference or earlier, never later, so two facts
explain a break: the first line that differs from the previous prompt (exact, from our side)
and the line the server's read reached (its token count scaled onto our lines, so a few
percent off). The first difference at or before the read is the cause and the report shows
it with one line of context either side. A first difference well after the read — or no
difference at all — means the text did not break the cache: on Claude a breakpoint's TTL ran
out, on a single-context server (ds4, Ollama — `AbstractLLM.cache_scope`) another instance
used the server in between, which the tracker names, or the backend's own `note` applies
(ds4's reasoning effort changed). The report goes to the observer hub (`on_cache_break`) for
the console's Home tab, which draws it as a debug row, and to the log at NOTE. The prompt
lines are laid out like `log/prompts/<id>.md` (PromptManager.save_prompt_to_file), the dump
the report cites, so its line numbers can be looked up there.
"""
import difflib
import time
from dataclasses import dataclass, field

from common.logging import fine, note, trace

THRESHOLD = 90          # percent of the previous prompt's tokens a request must read back
CONTEXT_LINES = 1       # unchanged lines shown above and below the change
CHANGE_LINES = 6        # at most this many removed and this many added lines in the diff
LOOKAHEAD = 200         # lines after the first difference the diff is computed over
READ_SLACK = 0.03       # the read estimate's tolerance, as a share of the prompt's lines…
READ_SLACK_LINES = 10   # …and at least this many lines, the scaling being coarsest at the start


@dataclass(frozen=True)
class CacheBreak:
    """One explained cache miss. Lines are 1-based; `first_diff_line` is None when the
    previous prompt is a prefix of this one (or the same), `diff` is empty then."""
    model: str
    cached_tokens: int
    prompt_tokens: int
    previous_tokens: int
    lines: int
    read_line: int
    first_diff_line: int | None
    cause: str
    diff: str = ""
    seconds_since_previous: float = 0.0
    dump: str | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def pct(self) -> int:
        return round(self.cached_tokens / self.prompt_tokens * 100) if self.prompt_tokens else 0

    def summary(self) -> str:
        return (f"{self.cached_tokens}/{self.prompt_tokens} tokens read back ({self.pct}%), "
                f"{self.previous_tokens} sent {self._ago()}. {self.cause}")

    def text(self) -> str:
        """The report as the console draws it under its header: the summary, then the diff."""
        return f"{self.summary()}\n{self.diff}" if self.diff else self.summary()

    def _ago(self) -> str:
        seconds = self.seconds_since_previous
        if seconds < 90:
            return f"{seconds:.0f} s before"
        if seconds < 5400:
            return f"{seconds / 60:.0f} min before"
        return f"{seconds / 3600:.1f} h before"


class CacheBreakTracker:
    """One per LLM instance: the previous prompt it sent, and the verdict on each new one."""

    _last_requester: dict[str, str] = {}    # single-context server → the instance that used it last

    def __init__(self, name: str, scope: str | None = None):
        self.name = name
        self.scope = scope
        self._previous: tuple[list[str], int, float] | None = None

    @staticmethod
    def flatten(messages: list[dict]) -> list[str]:
        """The prompt as the lines of its dump (`PromptManager._format_messages` without the
        image files), so a line number here is a line of `log/prompts/<id>.md`."""
        parts = []
        for msg in messages:
            role = str(msg.get("role", "unknown")).upper()
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(block.get("text", "") if block.get("type") == "text" else "[IMAGE]"
                                    for block in content if isinstance(block, dict))
            parts.append(f"# [{role}]\n\n{content}")
        return "\n\n---\n\n".join(parts).split("\n")

    def record(self, messages: list[dict], cached_tokens: int, prompt_tokens: int,
               note_text: str | None = None) -> CacheBreak | None:
        """The verdict on a request the server has answered: a report when its cache broke,
        None when it held or when this is the instance's first request. `note_text` is what
        the backend knows about the request that the prompt text does not say (ds4's effort
        change); it joins the cause when the text is clean."""
        now = time.time()
        lines = self.flatten(messages)
        previous, self._previous = self._previous, (lines, prompt_tokens, now)
        other = None
        if self.scope:
            other = self._last_requester.get(self.scope)
            self._last_requester[self.scope] = self.name
            if other == self.name:
                other = None
        if previous is None:
            trace(f"{self.name} cache: first request of this instance — nothing to compare the prompt against")
            return None
        if prompt_tokens <= 0:
            return None
        previous_lines, previous_tokens, previous_at = previous
        expected = min(previous_tokens, prompt_tokens)
        if cached_tokens >= expected * THRESHOLD / 100:
            fine(f"{self.name} cache held: {cached_tokens} of the {expected} tokens sent before read back")
            return None
        report = self._explain(lines, previous_lines, cached_tokens, prompt_tokens, previous_tokens,
                               now - previous_at, other, note_text)
        note(f"Cache break on {self.name}: {report.summary()}"
             + (f" Prompt: {report.dump}" if report.dump else "")
             + (f"\n{report.diff}" if report.diff else ""))
        from assistant.AssistantEvents import observers
        observers().on_cache_break(report)
        return report

    def _explain(self, lines, previous_lines, cached_tokens, prompt_tokens, previous_tokens,
                 seconds_since_previous, other, note_text) -> CacheBreak:
        first = self._first_difference(previous_lines, lines)
        read_line = max(1, round(cached_tokens / prompt_tokens * len(lines)))
        slack = max(READ_SLACK_LINES, round(len(lines) * READ_SLACK))
        diff = ""
        covered = (f"the cache covered the prompt to about line {read_line} of {len(lines)}" if cached_tokens
                   else "nothing was read back")
        if first is not None and first + 1 <= read_line + slack:
            cause = (f"{covered}; the prompt changed at line {first + 1} of {len(lines)}, so the cache could not "
                     f"reach past it." if not cached_tokens else
                     f"{covered}; the prompt changed at line {first + 1}, so the change broke it.")
            cause = cause[0].upper() + cause[1:]
            diff = self._diff(previous_lines, lines, first)
        else:
            where = (f"the first difference is at line {first + 1}" if first is not None
                     else "the previous prompt is a prefix of this one")
            reasons = [f"{covered} and the text is unchanged up to there ({where}), so the text did not break it"]
            if other:
                reasons.append(f"{other} used the server in between")
            if note_text:
                reasons.append(note_text)
            if not other and not note_text:
                reasons.append("the cache entry expired or was evicted")
            cause = "; ".join(reasons) + "."
            cause = cause[0].upper() + cause[1:]
        from assistant.prompt.PromptManager import PromptManager
        return CacheBreak(model=self.name, cached_tokens=cached_tokens, prompt_tokens=prompt_tokens,
                          previous_tokens=previous_tokens, lines=len(lines), read_line=read_line,
                          first_diff_line=None if first is None else first + 1, cause=cause, diff=diff,
                          seconds_since_previous=seconds_since_previous, dump=PromptManager.last_prompt_file)

    @staticmethod
    def _first_difference(old: list[str], new: list[str]) -> int | None:
        """Index of the first line of `new` that is not the same line of `old`; None when
        `old` is a prefix of `new` — then nothing the server had was changed."""
        for index, (a, b) in enumerate(zip(old, new)):
            if a != b:
                return index
        return None if len(old) <= len(new) else len(new)

    @classmethod
    def _diff(cls, old: list[str], new: list[str], first: int) -> str:
        """The first change as -/+ lines with CONTEXT_LINES of context either side, capped at
        CHANGE_LINES each way; context lines are indented so a line of their own that starts
        with - or + is not read as part of the diff."""
        from common.logging import masked_for_log
        start = max(0, first - CONTEXT_LINES)
        out = ["  " + line for line in new[start:first]]
        matcher = difflib.SequenceMatcher(None, old[first:first + LOOKAHEAD], new[first:first + LOOKAHEAD],
                                          autojunk=False)
        removed, added, resume = [], [], first
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal" and (removed or added):
                break   # the first run of changes ends here
            if tag != "equal":
                removed.extend(old[first + i1:first + i2])
                added.extend(new[first + j1:first + j2])
                resume = first + j2
        cut = len(removed) > CHANGE_LINES or len(added) > CHANGE_LINES
        out += ["-" + line for line in removed[:CHANGE_LINES]]
        out += ["+" + line for line in added[:CHANGE_LINES]]
        if cut:
            out.append(f"  … ({len(removed)} removed, {len(added)} added lines in all)")
        else:
            out += ["  " + line for line in new[resume:resume + CONTEXT_LINES]]
        return masked_for_log("\n".join(out))
