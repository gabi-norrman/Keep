"""The console's one look for a diff: red removed lines, green added lines, a `- ` / `+ `
gutter, a near-black tint of the same hue across the whole line, and the characters that
actually changed lit with a brighter background when a removed line pairs with an added one. The Memory tab draws its snapshot diff with these colours
and `inline_diff`; the chat pane renders a `-`/`+` listing (a prompt-section delta)
through `diff_spans`.
"""
import difflib
from typing import List, Tuple

from common.logging import trace
from interfaces.console.Theme import COLOR_GREEN, COLOR_RED, mix
from interfaces.console.framework.TUIScreen import Color, TUIScreen
from interfaces.console.tabs.TelegramHtmlParser import StyledSpan

_GROUND = TUIScreen.BACKGROUND
COLOR_REMOVED = mix(_GROUND, COLOR_RED, 0.72)
COLOR_ADDED = COLOR_GREEN
COLOR_GUTTER_ADD = mix(_GROUND, COLOR_GREEN, 0.8)
COLOR_GUTTER_DEL = COLOR_RED
COLOR_ADDED_HIGHLIGHT_BG = mix(_GROUND, COLOR_GREEN, 0.32)     # the characters that changed
COLOR_REMOVED_HIGHLIGHT_BG = mix(_GROUND, COLOR_RED, 0.34)
COLOR_ADDED_LINE_BG = mix(_GROUND, COLOR_GREEN, 0.1)           # the rest of the line
COLOR_REMOVED_LINE_BG = mix(_GROUND, COLOR_RED, 0.11)

Ranges = List[Tuple[int, int]]


def inline_diff(old_line: str, new_line: str) -> Tuple[Ranges, Ranges]:
    """The (start, end) character ranges that differ between two lines, per side."""
    sm = difflib.SequenceMatcher(None, old_line, new_line)
    old_ranges, new_ranges = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            continue
        if i1 < i2:
            old_ranges.append((i1, i2))
        if j1 < j2:
            new_ranges.append((j1, j2))
    return old_ranges, new_ranges


def diff_spans(text: str) -> List[StyledSpan]:
    """Spans for a text whose `-`/`+` lines are a diff; other lines are left unstyled.
    Every span of a diff line carries the line's background (the renderer tints the rest
    of the row with the first span's). Within each run of removed lines followed by added
    ones, the k-th removed line pairs with the k-th added line and the pair's changed
    characters get the brighter highlight."""
    lines = text.split("\n")
    spans: List[StyledSpan] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(("-", "+")):
            spans.append(StyledSpan(text=lines[i] + "\n"))
            i += 1
            continue
        removed, added = [], []
        while i < len(lines) and lines[i].startswith("-"):
            removed.append(lines[i][1:])
            i += 1
        while i < len(lines) and lines[i].startswith("+"):
            added.append(lines[i][1:])
            i += 1
        highlights = [inline_diff(old, new) for old, new in zip(removed, added)]
        for k, line in enumerate(removed):
            ranges = highlights[k][0] if k < len(highlights) else []
            spans.extend(_line_spans("- ", line, COLOR_GUTTER_DEL, COLOR_REMOVED, COLOR_REMOVED_LINE_BG,
                                     COLOR_REMOVED_HIGHLIGHT_BG, ranges))
        for k, line in enumerate(added):
            ranges = highlights[k][1] if k < len(highlights) else []
            spans.extend(_line_spans("+ ", line, COLOR_GUTTER_ADD, COLOR_ADDED, COLOR_ADDED_LINE_BG,
                                     COLOR_ADDED_HIGHLIGHT_BG, ranges))
    if spans and spans[-1].text.endswith("\n"):
        spans[-1] = StyledSpan(text=spans[-1].text[:-1], fg=spans[-1].fg, bg=spans[-1].bg)
    if not any(s.fg for s in spans):
        trace("DiffText: no -/+ lines in the text, shown unstyled")
    return spans


def _line_spans(gutter: str, line: str, gutter_fg: Color, fg: Color, line_bg: Color, highlight_bg: Color,
                ranges: Ranges) -> List[StyledSpan]:
    spans = [StyledSpan(text=gutter, fg=gutter_fg, bg=line_bg)]
    pos = 0
    for start, end in ranges:
        if start > pos:
            spans.append(StyledSpan(text=line[pos:start], fg=fg, bg=line_bg))
        spans.append(StyledSpan(text=line[start:end], fg=fg, bg=highlight_bg))
        pos = end
    spans.append(StyledSpan(text=line[pos:] + "\n", fg=fg, bg=line_bg))
    return spans
