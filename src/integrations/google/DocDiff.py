"""The diff of two versions of a document's text, as one history row's content.

Pure functions, no I/O. The grain is the paragraph: a Google Doc's text export has one line per
paragraph, so the lines are compared, and a paragraph replaced by one that shares more than
half its characters is shown once with the change marked inline — a changed word in a long
paragraph must not print the paragraph twice. Paragraphs are compared with their list markers
stripped: the export numbers list items, so an item inserted near the top renumbers every one
after it, and a diff on the raw lines showed 181 added and 166 removed for one added word
(2026-10-15). Each hunk opens with the paragraph before it,
cut short, so the reader knows where in the document it happened. The whole is capped; the
first line carries the author and the counts, so it says what happened even when the hunks
are cut."""
import re
from difflib import SequenceMatcher
from typing import Optional

# Under MessageHistory.MAX_RENDER_CHARS (2000) with room for the "… and N more" tail: a feed row
# is a system row, cut at that length when the prompt is rendered, and the tail must survive.
DIFF_CHAR_LIMIT = 1900
CONTEXT_CHARS = 120
LINE_CHARS = 1500          # a whole paragraph added or removed is shown up to this
INLINE_RATIO = 0.5         # a 1:1 replace this similar is shown inline
WORD_SPLIT = re.compile(r"\S+|\s+")
LIST_MARKER = re.compile(r"^(?:\d+|[a-zA-Z]|[ivxIVX]+)[.)]\s+|^[*•\-–]\s+")


def paragraphs(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _key(line: str) -> str:
    """What a paragraph is compared by: its text without the list number or bullet in front."""
    return LIST_MARKER.sub("", line, count=1)


def tracking_note(name: str, text: str) -> str:
    """The row of a document seen for the first time, with nothing to diff against."""
    lines = paragraphs(text)
    words = sum(len(line.split()) for line in lines)
    return f"Google Doc «{name}» is now tracked: {len(lines)} paragraphs, {words} words. Its edits will follow here as diffs."


def describe_change(name: str, author: str, old_text: str, new_text: str, edits: int = 1,
                    limit: int = DIFF_CHAR_LIMIT) -> Optional[str]:
    """The row for what `edits` revisions by `author` changed: None when the texts are the same paragraphs."""
    old, new = paragraphs(old_text), paragraphs(new_text)
    hunks, added, removed, edited = _hunks(old, new)
    if not hunks:
        return None
    counts = ", ".join(f"{n} {what}" for n, what in ((edited, "edited"), (added, "added"), (removed, "removed")) if n)
    sessions = f" ({edits} edits)" if edits > 1 else ""
    head = f"Google Doc «{name}» edited by {author}{sessions}: {counts} paragraph(s)"
    body, left = _fit(hunks, limit - len(head) - 1)
    if left:
        body.append(f"… and {left} more changed paragraph(s)")
    return "\n".join([head, *body])


def _hunks(old: list[str], new: list[str]) -> tuple[list[list[str]], int, int, int]:
    """The hunks, each a list of lines (`  context`, `- old`, `+ new`, `~ inline`), with the
    counts of added, removed and edited paragraphs."""
    hunks: list[list[str]] = []
    added = removed = edited = 0
    matcher = SequenceMatcher(None, [_key(p) for p in old], [_key(p) for p in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunk = [f"  {_cut(old[i1 - 1])}"] if i1 > 0 else []
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1 and _similar(old[i1], new[j1]):
            hunk.append(f"~ {_inline(old[i1], new[j1])}")
            edited += 1
        else:
            hunk += [f"- {_cut(line, LINE_CHARS)}" for line in old[i1:i2]] + [f"+ {_cut(line, LINE_CHARS)}" for line in new[j1:j2]]
            removed += i2 - i1
            added += j2 - j1
        hunks.append(hunk)
    return hunks, added, removed, edited


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() > INLINE_RATIO


def _inline(old: str, new: str) -> str:
    """`old` rewritten with the change marked: `[-gone-]{+new+}` on the words that differ."""
    a, b = WORD_SPLIT.findall(old), WORD_SPLIT.findall(new)
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append("".join(a[i1:i2]))
            continue
        gone, came = "".join(a[i1:i2]).strip(), "".join(b[j1:j2]).strip()
        if gone:
            out.append(f"[-{gone}-]")
        if came:
            out.append(("" if not gone else " ") + f"{{+{came}+}}")
        out.append(" ")
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _cut(line: str, limit: int = CONTEXT_CHARS) -> str:
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _fit(hunks: list[list[str]], room: int) -> tuple[list[str], int]:
    """As many whole hunks as fit in `room` chars, and how many changed paragraphs were left out."""
    shown: list[str] = []
    used = 0
    for index, hunk in enumerate(hunks):
        size = sum(len(line) + 1 for line in hunk)
        if used + size > room and shown:
            left = sum(1 for h in hunks[index:] for line in h if not line.startswith("  "))
            return shown, left
        shown += hunk
        used += size
    return shown, 0
