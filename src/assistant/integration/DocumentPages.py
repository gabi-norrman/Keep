"""The pages of a long document and the find inside it: pure text functions any folder that reads documents shares.

A command's result is kept whole in the history up to `MessageHistory.COMMAND_RESULT_CHARS`
(6,000) and cut after that, so a document is read in pages of at most PAGE_CHARS that the LLM
asks for one at a time. `pages` cuts a text at paragraph boundaries; `find` is the content
search that works on any backend: the lines carrying every given word, each with its page
and the `[p. N]` / `[slide N]` marker line a reader put before it."""
import re
from dataclasses import dataclass

# Under MessageHistory.COMMAND_RESULT_CHARS (6,000) with the header of a page over it.
PAGE_CHARS = 5000
FIND_LIMIT = 20
SNIPPET_CHARS = 200
MARKER_RE = re.compile(r"^\[(?:p\.|slide) (\d+)\]$")


@dataclass(frozen=True)
class Hit:
    page: int                   # the page of `pages()` the line is on
    marker: str                 # the PDF page or slide the line is under, "" for plain text
    line: str


def pages(text: str, limit: int = PAGE_CHARS) -> list[str]:
    """The text cut into pages of at most `limit` characters, at a blank line when one is in
    reach, else a line end, else a space, else hard. An empty text is one empty page."""
    text = text.strip()
    if not text:
        return [""]
    out = []
    while len(text) > limit:
        window = text[:limit]
        cut = next((at for at in (window.rfind("\n\n", limit // 4), window.rfind("\n", limit // 2),
                                  window.rfind(" ", limit * 3 // 4)) if at > 0), limit)
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    out.append(text)
    return out


def find(text: str, words: list[str], limit: int = FIND_LIMIT) -> list[Hit]:
    """The lines carrying every word (case aside), each with its page of `pages()` and the
    `[p. N]` / `[slide N]` marker it stands under; at most `limit` hits."""
    wanted = [w.lower() for w in words if w.strip()]
    if not wanted:
        return []
    hits: list[Hit] = []
    marker = ""
    for number, page in enumerate(pages(text), 1):
        for line in page.splitlines():
            stripped = line.strip()
            if match := MARKER_RE.match(stripped):
                marker = stripped
                continue
            lowered = stripped.lower()
            if stripped and all(w in lowered for w in wanted):
                hits.append(Hit(number, marker, snippet(stripped, wanted[0])))
                if len(hits) >= limit:
                    return hits
    return hits


def snippet(line: str, word: str, limit: int = SNIPPET_CHARS) -> str:
    """The line, or the part of it around the first `word` when it is longer than `limit`."""
    if len(line) <= limit:
        return line
    at = line.lower().find(word)
    start = max(0, min(at - limit // 3, len(line) - limit))
    piece = line[start:start + limit]
    return ("…" if start > 0 else "") + piece + ("…" if start + limit < len(line) else "")
