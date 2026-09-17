"""LogTab cuts and wraps its lines in terminal cells: a colour code takes none, an emoji or
a CJK glyph two, and a glyph that would straddle the cut is dropped rather than half-drawn."""
import pytest

from interfaces.console.tabs.LogTab import LogTab

RED, RESET = "\x1b[31m", "\x1b[0m"
SMILE = "\U0001F600"


@pytest.fixture
def tab():
    t = LogTab()
    t.display_height = 20
    return t


class TestTruncateAnsiAware:
    def test_colour_codes_take_no_cells(self, tab):
        assert tab._truncate_ansi_aware(f"{RED}ab{RESET}cdef", 3) == f"{RED}ab{RESET}c"

    def test_a_wide_glyph_counts_two_cells(self, tab):
        assert tab._truncate_ansi_aware(f"ab{SMILE}cd", 4) == f"ab{SMILE}"

    def test_a_glyph_that_would_straddle_the_cut_is_dropped(self, tab):
        assert tab._truncate_ansi_aware(f"ab{SMILE}cd", 3) == "ab"


class TestWrapAnsiAware:
    def test_overflow_is_measured_in_cells(self, tab):
        # Five characters but nine cells: measured with len() the line would stay whole.
        assert tab._wrap_ansi_aware(f"{SMILE}{SMILE} {SMILE}{SMILE}", 5) == [f"{SMILE}{SMILE}", f"{SMILE}{SMILE}"]

    def test_a_variation_selector_does_not_shift_the_token_mapping(self, tab):
        # word_wrap drops the VS16 before measuring; the tokens it is mapped back onto must be
        # the same text, or the tail of the line is lost ("ab☀️ cd efgh" used to end at "cd e").
        assert tab._wrap_ansi_aware("ab\u2600\ufe0f cd efgh", 5) == ["ab\u2600", "cd", "efgh"]
        assert tab._wrap_ansi_aware(f"{RED}ab\u2600\ufe0f cd{RESET} efgh", 5) == [f"{RED}ab\u2600", f"{RED}cd", "efgh"]
