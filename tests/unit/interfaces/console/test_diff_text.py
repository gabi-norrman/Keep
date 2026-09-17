"""DiffText.diff_spans: -/+ lines become gutter + coloured text, paired lines light their
changed characters, everything else stays unstyled."""
from interfaces.console.widgets.DiffText import (COLOR_ADDED, COLOR_ADDED_HIGHLIGHT_BG, COLOR_ADDED_LINE_BG,
                                                 COLOR_GUTTER_ADD, COLOR_GUTTER_DEL, COLOR_REMOVED,
                                                 COLOR_REMOVED_HIGHLIGHT_BG, COLOR_REMOVED_LINE_BG, diff_spans)


def rendered(spans):
    return "".join(s.text for s in spans)


def test_plain_lines_stay_unstyled():
    spans = diff_spans("just a note\nsecond line")
    assert rendered(spans) == "just a note\nsecond line"
    assert all(s.fg is None and s.bg is None for s in spans)


def test_removed_and_added_lines_get_gutter_and_colour():
    assert [(s.text, s.fg) for s in diff_spans("Section `X` updated:\n-gone")] == [
        ("Section `X` updated:\n", None), ("- ", COLOR_GUTTER_DEL), ("gone", COLOR_REMOVED)]
    assert [(s.text, s.fg, s.bg) for s in diff_spans("+here")] == [
        ("+ ", COLOR_GUTTER_ADD, COLOR_ADDED_LINE_BG), ("here", COLOR_ADDED, COLOR_ADDED_LINE_BG)]


def test_paired_lines_highlight_only_what_changed():
    spans = diff_spans('-"Temp": 22.3 °C\n+"Temp": 22.2 °C')
    lit = [(s.text, s.fg, s.bg) for s in spans if s.bg not in (COLOR_REMOVED_LINE_BG, COLOR_ADDED_LINE_BG)]
    assert lit == [("3", COLOR_REMOVED, COLOR_REMOVED_HIGHLIGHT_BG), ("2", COLOR_ADDED, COLOR_ADDED_HIGHLIGHT_BG)]
    assert rendered(spans) == '- "Temp": 22.3 °C\n+ "Temp": 22.2 °C'


def test_blocks_pair_by_position_and_unpaired_lines_are_plain_coloured():
    spans = diff_spans("-a: 1\n-b: 1\n+a: 2\n+b: 2\n+c: 3")
    lit = [s.text for s in spans if s.bg in (COLOR_REMOVED_HIGHLIGHT_BG, COLOR_ADDED_HIGHLIGHT_BG)]
    assert lit == ["1", "1", "2", "2"]
    assert rendered(spans) == "- a: 1\n- b: 1\n+ a: 2\n+ b: 2\n+ c: 3"


def test_two_separate_blocks_pair_independently():
    spans = diff_spans("-x: 1\n+x: 2\nnote\n-y: 1\n+y: 9")
    assert [s.text for s in spans if s.bg in (COLOR_REMOVED_HIGHLIGHT_BG, COLOR_ADDED_HIGHLIGHT_BG)] == ["1", "2", "1", "9"]
