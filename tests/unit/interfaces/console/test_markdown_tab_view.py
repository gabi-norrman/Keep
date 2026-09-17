import pytest

from interfaces.console.tabs.MarkdownTabView import MarkdownTabView, MARKDOWN_COLORS
from interfaces.console.framework.TUIScreen import TUIScreen


class _MD(MarkdownTabView):
    """Concrete subclass — MarkdownTabView's draw/handle_input are abstract."""

    def draw(self, screen, frame):
        pass

    def handle_input(self, key):
        return False


@pytest.fixture
def tab():
    return _MD()


@pytest.fixture
def screen():
    return TUIScreen(40, 5)


def cell(screen, x, y):
    return screen.next_frame[y - 1][x - 1]


def row_text(screen, y, start, end):
    return "".join(c.char for c in screen.next_frame[y - 1][start - 1:end])


class TestDrawFormattedLineHeaders:
    def test_h1_is_bold_with_h1_color(self, tab, screen):
        tab.draw_formatted_line(screen, 1, 1, "# Title", max_width=38)
        c = cell(screen, 1, 1)
        assert c.char == "#"
        assert c.style.bold
        assert c.style.fg == MARKDOWN_COLORS["h1"]

    def test_h3_uses_h3_color(self, tab, screen):
        tab.draw_formatted_line(screen, 1, 1, "### Sub", max_width=38)
        assert cell(screen, 1, 1).style.fg == MARKDOWN_COLORS["h3"]

    def test_header_text_rendered(self, tab, screen):
        tab.draw_formatted_line(screen, 1, 1, "## Hello", max_width=38)
        assert row_text(screen, 1, 1, 8) == "## Hello"


class TestDrawFormattedLineCodeBlock:
    def test_fence_toggles_code_block_on(self, tab, screen):
        assert tab.in_code_block is False
        tab.draw_formatted_line(screen, 1, 1, "```python", max_width=38)
        assert tab.in_code_block is True
        assert cell(screen, 1, 1).style.fg == MARKDOWN_COLORS["code_block"]

    def test_second_fence_toggles_off(self, tab, screen):
        tab.draw_formatted_line(screen, 1, 1, "```", max_width=38)
        tab.draw_formatted_line(screen, 1, 2, "```", max_width=38)
        assert tab.in_code_block is False

    def test_line_inside_code_block_uses_code_color(self, tab, screen):
        tab.in_code_block = True
        tab.draw_formatted_line(screen, 1, 1, "x = 1", max_width=38)
        assert cell(screen, 1, 1).style.fg == MARKDOWN_COLORS["code_block"]
        assert not cell(screen, 1, 1).style.bold


class TestSeekCodeBlock:
    LINES = ["# Title", "```python", "x = 1", "```", "after", "  ```", "y = 2"]

    @pytest.mark.parametrize("start_line, inside", [
        (0, False), (1, False), (2, True), (3, True), (4, False), (6, True), (7, True),
    ])
    def test_state_is_the_fence_parity_of_the_lines_scrolled_past(self, tab, start_line, inside):
        tab.in_code_block = not inside   # the seek must not depend on the previous frame
        tab.seek_code_block(self.LINES, start_line)
        assert tab.in_code_block is inside

    def test_a_window_opened_inside_a_block_draws_its_first_line_as_code(self, tab, screen):
        tab.seek_code_block(self.LINES, 2)
        tab.draw_formatted_line(screen, 1, 1, "x = 1", max_width=38)
        assert cell(screen, 1, 1).style.fg == MARKDOWN_COLORS["code_block"]


class TestDrawFormattedLineLists:
    def test_bullet_renders_bullet_glyph(self, tab, screen):
        tab.draw_formatted_line(screen, 1, 1, "- item", max_width=38)
        c = cell(screen, 1, 1)
        assert c.char == "•"
        assert c.style.bold
        assert c.style.fg == MARKDOWN_COLORS["bullet"]

    def test_numbered_list_renders_number(self, tab, screen):
        tab.draw_formatted_line(screen, 1, 1, "1. item", max_width=38)
        assert row_text(screen, 1, 1, 2) == "1."
        assert cell(screen, 1, 1).style.fg == MARKDOWN_COLORS["bullet"]


class TestDrawInlineFormatted:
    def test_plain_text_uses_normal_color(self, tab, screen):
        screen.move_cursor(1, 1)
        tab.draw_inline_formatted(screen, "hello", 38)
        assert row_text(screen, 1, 1, 5) == "hello"
        assert cell(screen, 1, 1).style.fg == MARKDOWN_COLORS["normal"]

    def test_inline_code_colored(self, tab, screen):
        screen.move_cursor(1, 1)
        tab.draw_inline_formatted(screen, "see `code` now", 38)
        # "see " occupies cols 1-4; "code" starts at col 5.
        assert cell(screen, 5, 1).char == "c"
        assert cell(screen, 5, 1).style.fg == MARKDOWN_COLORS["code"]

    def test_bold_segment(self, tab, screen):
        screen.move_cursor(1, 1)
        tab.draw_inline_formatted(screen, "a **b** c", 38)
        # "a " then bold "b" at col 3.
        assert cell(screen, 3, 1).char == "b"
        assert cell(screen, 3, 1).style.bold
        assert cell(screen, 3, 1).style.fg == MARKDOWN_COLORS["bold"]

    def test_italic_segment(self, tab, screen):
        screen.move_cursor(1, 1)
        tab.draw_inline_formatted(screen, "a *b* c", 38)
        assert cell(screen, 3, 1).char == "b"
        assert cell(screen, 3, 1).style.italic

    def test_overflow_truncated_with_ellipsis(self, tab, screen):
        screen.move_cursor(1, 1)
        tab.draw_inline_formatted(screen, "a" * 50, 10)
        # limit = max_width - 3 = 7 visible chars, then "..."
        assert row_text(screen, 1, 1, 10) == "aaaaaaa..."


class TestWideGlyphs:
    def test_overflow_is_cut_in_cells_before_the_ellipsis(self, tab, screen):
        """Three CJK glyphs are six cells: measured with len() they fit in 5 and the row
        overshot its width. Two cells are left before the ellipsis, so one glyph fits and
        the next, which would straddle the cut, is dropped."""
        screen.move_cursor(1, 1)
        tab.draw_inline_formatted(screen, "日本語", 5)
        assert row_text(screen, 1, 1, 5) == "日..."
        assert cell(screen, 6, 1).char == " "
