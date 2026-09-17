import pytest

from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.ScrollableTabView import ScrollableTabView, draw_scrollbar


class _ConcreteScrollable(ScrollableTabView):
    """Minimal concrete subclass — ScrollableTabView's draw/handle_input are abstract."""

    def draw(self, screen, frame):
        pass

    def handle_input(self, key):
        return False


@pytest.fixture
def view():
    v = _ConcreteScrollable()
    v.display_height = 20
    return v


class TestHandleScrollInput:
    def test_up_increments_offset(self, view):
        assert view.handle_scroll_input("up", 100) is True
        assert view.scroll_offset == 1

    def test_up_clamped_at_max_scroll(self, view):
        view.scroll_offset = 80  # max for total=100, height=20
        view.handle_scroll_input("up", 100)
        assert view.scroll_offset == 80

    def test_down_decrements_offset(self, view):
        view.scroll_offset = 5
        view.handle_scroll_input("down", 100)
        assert view.scroll_offset == 4

    def test_down_clamped_at_zero(self, view):
        view.scroll_offset = 0
        view.handle_scroll_input("down", 100)
        assert view.scroll_offset == 0

    def test_pageup_adds_display_height(self, view):
        view.handle_scroll_input("pageup", 100)
        assert view.scroll_offset == 20

    def test_pageup_clamped_at_max_scroll(self, view):
        view.scroll_offset = 70
        view.handle_scroll_input("pageup", 100)  # 70+20=90 -> clamp to 80
        assert view.scroll_offset == 80

    def test_pagedown_subtracts_display_height(self, view):
        view.scroll_offset = 30
        view.handle_scroll_input("pagedown", 100)
        assert view.scroll_offset == 10

    def test_pagedown_clamped_at_zero(self, view):
        view.scroll_offset = 5
        view.handle_scroll_input("pagedown", 100)
        assert view.scroll_offset == 0

    def test_w_toggles_line_wrap_and_resets_scroll(self, view):
        view.scroll_offset = 40
        assert view.line_wrap is False
        view.handle_scroll_input("w", 100)
        assert view.line_wrap is True
        assert view.scroll_offset == 0
        view.handle_scroll_input("w", 100)
        assert view.line_wrap is False

    def test_unknown_key_returns_false(self, view):
        view.scroll_offset = 7
        assert view.handle_scroll_input("q", 100) is False
        assert view.scroll_offset == 7

    def test_scroll_total_is_the_raw_count_until_a_frame_has_wrapped_the_lines(self, view):
        # 10 raw lines wrap to 50 display lines: the keys must clamp against the wrapped count,
        # or a wrapped tab could never scroll past what fits unwrapped.
        assert view.scroll_total(10) == 10
        assert view.handle_scroll_input('up', view.scroll_total(10))
        assert view.scroll_offset == 0
        view.total_lines = 50
        assert view.scroll_total(10) == 50
        view.handle_scroll_input('pageup', view.scroll_total(10))
        assert view.scroll_offset == 20

    def test_no_scroll_when_content_fits(self, view):
        # total_lines <= display_height => max_scroll is 0
        view.handle_scroll_input("up", 10)
        assert view.scroll_offset == 0


class TestWordWrap:
    def test_short_line_unchanged(self):
        assert ScrollableTabView.word_wrap("hi", 5) == ["hi"]

    def test_exact_width_not_wrapped(self):
        assert ScrollableTabView.word_wrap("hello", 5) == ["hello"]

    def test_wrap_at_space_boundary(self):
        assert ScrollableTabView.word_wrap("hello world", 5) == ["hello", "world"]

    def test_multiple_words_packed_per_line(self):
        assert ScrollableTabView.word_wrap("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]

    def test_long_word_hard_split(self):
        assert ScrollableTabView.word_wrap("abcdefgh", 3) == ["abc", "def", "gh"]

    def test_no_space_within_width_hard_breaks(self):
        assert ScrollableTabView.word_wrap("abcdefg hi", 4) == ["abcd", "efg", "hi"]


class TestDrawScrollbar:
    X, START_Y, HEIGHT = 10, 3, 10

    def _column(self, total_lines, scroll_offset):
        screen = TUIScreen(10, 12)
        draw_scrollbar(screen, self.X, self.START_Y, total_lines, self.HEIGHT, scroll_offset)
        return "".join(screen.next_frame[self.START_Y - 1 + i][self.X - 1].char for i in range(self.HEIGHT))

    def test_thumb_sits_at_the_bottom_when_scrolled_to_the_end(self):
        assert self._column(100, 0) == "│" * 9 + "┃"

    def test_thumb_sits_at_the_top_when_scrolled_to_the_start(self):
        assert self._column(100, 90) == "┃" + "│" * 9

    def test_thumb_grows_with_the_visible_share(self):
        assert self._column(20, 5) == "││┃┃┃┃┃│││"

    def test_nothing_drawn_when_the_content_fits(self):
        assert self._column(10, 0) == " " * 10


def _line(screen, y0):
    return "".join(c.char for c in screen.next_frame[y0]).rstrip()


class TestDrawBottomBar:
    def test_hints_left_and_the_position_only_when_the_content_overflows(self, view):
        view.scroll_offset = 7
        screen = TUIScreen(60, 10)
        view.draw_bottom_bar(screen, " w wrap  ↑↓ scroll ", total_lines=100, display_height=20)
        line = _line(screen, 8)
        assert line.startswith(" w wrap  ↑↓ scroll ")
        assert line.endswith("7 lines from bottom")
        screen = TUIScreen(60, 10)
        view.draw_bottom_bar(screen, " w wrap ", total_lines=5, display_height=20)
        assert _line(screen, 8) == " w wrap"


class TestWordWrapWidth:
    def test_wraps_by_cells_not_characters(self):
        from interfaces.console.tabs.ScrollableTabView import ScrollableTabView
        assert ScrollableTabView.word_wrap("\U0001F600\U0001F600 ab", 4) == ["\U0001F600\U0001F600", "ab"]

    def test_hard_break_never_splits_a_glyph(self):
        from interfaces.console.tabs.ScrollableTabView import ScrollableTabView
        assert ScrollableTabView.word_wrap("a\U0001F600b", 2) == ["a", "\U0001F600", "b"]
