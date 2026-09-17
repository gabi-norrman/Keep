"""The screen turns a cell's `href` into an OSC 8 hyperlink the terminal can open on click."""
from interfaces.console.framework.TUIScreen import Color, Style, TUIScreen

URL = "https://www.apple.com/se/shop/buy-mac/mac-studio"
OPEN = TUIScreen.link_open(URL)


def _frame(screen: TUIScreen) -> str:
    return screen.get_frame(use_sync=False).decode()


def test_linked_run_is_wrapped_in_osc8():
    screen = TUIScreen(40, 2)
    screen.write_at(1, 1, "Store: ")
    screen.link(URL)
    screen.underline()
    screen.write("apple.com")
    screen.link(None)
    screen.write(" now")
    out = _frame(screen)
    assert OPEN + "apple.com" + TUIScreen.LINK_CLOSE in out
    assert out.count(TUIScreen.OSC + "8;") == 2   # one open, one close
    assert out.index("Store:") < out.index(OPEN) < out.index("now")


def test_link_open_carries_a_stable_id_and_encodes_what_osc_cannot_carry():
    assert TUIScreen.link_open(URL) == TUIScreen.link_open(URL)
    assert TUIScreen.link_open("https://a.se/x") != TUIScreen.link_open("https://a.se/y")
    # non-ASCII and ';' are percent-encoded, still the same URL; a control character is dropped
    assert TUIScreen.link_open("https://a.se/ö;\x07x").endswith("https://a.se/%C3%B6%3Bx" + TUIScreen.ST)
    assert TUIScreen.link_open("https://sv.wikipedia.org/wiki/Göteborg").endswith(
        "https://sv.wikipedia.org/wiki/G%C3%B6teborg" + TUIScreen.ST)
    # what is already encoded, and every URL delimiter, stays as it is
    plain = "https://a.se/p?q=a+b&x=%20#frag"
    assert TUIScreen.link_open(plain).endswith(plain + TUIScreen.ST)
    assert TUIScreen.link_open("https://a.se/a b").endswith("https://a.se/a%20b" + TUIScreen.ST)


def test_href_is_part_of_the_style_diff():
    screen = TUIScreen(20, 1)
    screen.write_at(1, 1, "apple.com")
    _frame(screen)
    screen.clear_buffer()
    screen.link(URL)
    screen.write_at(1, 1, "apple.com")
    out = _frame(screen)
    assert OPEN in out                        # same text, new link: emitted
    screen.clear_buffer()
    screen.link(URL)
    screen.write_at(1, 1, "apple.com")
    assert screen.get_frame(use_sync=False) == b""   # unchanged: nothing emitted


def test_style_equality_and_copies_carry_href():
    a = Style(fg=Color(1, 2, 3), href=URL)
    assert a == a.copy()
    assert a != Style(fg=Color(1, 2, 3))
    assert not Style(href=URL).is_default()
    b = Style()
    b.copy_from(a)
    assert b.href == URL
    b.reset()
    assert b.href is None
