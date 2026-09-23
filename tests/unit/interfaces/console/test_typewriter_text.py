"""TypewriterText: the time-based reveal and its sweeping cursor."""
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.widgets.TypewriterText import TypewriterText


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


def test_reveals_by_elapsed_time_at_cps():
    clock = Clock()
    tw = TypewriterText("abcdefghij", cps=10, clock=clock)
    assert tw.visible_text() == ""
    clock.tick(0.35)
    assert tw.visible_text() == "abc"
    assert not tw.done
    clock.tick(10)
    assert tw.visible_text() == "abcdefghij"
    assert tw.done


def test_reveal_false_shows_everything_at_once():
    tw = TypewriterText("hello", reveal=False, clock=Clock())
    assert tw.visible_text() == "hello"
    assert tw.done


def test_reveal_false_stays_instant_as_the_text_grows():
    clock = Clock()
    tw = TypewriterText("Hel", reveal=False, clock=clock)
    tw.set_text("Hello world, this is streaming in")
    assert tw.visible_text() == "Hello world, this is streaming in"
    tw.append(" and more")
    assert tw.done


def test_empty_text_is_done():
    assert TypewriterText("", clock=Clock()).done


def test_append_after_a_quiet_spell_types_out_instead_of_appearing():
    clock = Clock()
    tw = TypewriterText("ab", cps=10, clock=clock)
    clock.tick(5)
    assert tw.visible_text() == "ab"
    clock.tick(60)          # nobody looked for a minute
    tw.append("cdefgh")
    assert tw.visible_text() == "ab"
    clock.tick(0.25)
    visible = tw.visible_text()
    assert visible.startswith("ab") and len(visible) < 8


def test_set_text_keeps_what_was_revealed():
    clock = Clock()
    tw = TypewriterText("hello", cps=10, clock=clock)
    clock.tick(0.35)
    assert tw.visible_text() == "hel"
    tw.set_text("hello world")
    assert tw.visible_text() == "hel"
    tw.set_text("he")
    assert tw.visible_text() == "he"
    assert tw.done


def test_backlog_speeds_the_reveal_up():
    """A fast stream must not trail further and further behind the base cps."""
    clock = Clock()
    tw = TypewriterText("x" * 400, cps=10, clock=clock)
    clock.tick(1.0)
    # 10 cps alone would show 10 chars; the backlog term shows far more.
    assert len(tw.visible_text()) > 100


def test_cursor_draws_at_the_given_cell_and_hides_when_none():
    screen = TUIScreen(20, 3)
    tw = TypewriterText("ab", reveal=False, clock=Clock())
    fg = Color(1, 2, 3)
    tw.draw_cursor(screen, 0, (5, 2), fg=fg, row_start_x=3)
    cell = screen.next_frame[1][4]
    assert cell.style.bg == fg
    screen.clear_buffer()
    tw.draw_cursor(screen, 1, None, fg=fg)
    # Only the fading trail remains; the head is gone.
    assert screen.next_frame[1][4].style.bg != fg
