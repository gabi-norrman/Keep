"""HomeTab: the chat composer takes every key, but Tab walks the tabs except while a secret
prompt owns the composer (Tab reveals the masked value there)."""
from interfaces.console.tabs.HomeTab import HomeTab
from interfaces.console.widgets.ChatPane import ChatPane


class FakeConsole:
    waiting_for_user = False


def test_tab_is_claimed_only_while_a_secret_prompt_owns_the_composer():
    pane = ChatPane()
    tab = HomeTab(FakeConsole(), pane)
    assert tab.captures_text and not tab.claims_tab
    first = pane.add_secret_prompt("t1", "Password", "")
    second = pane.add_secret_prompt("t2", "Amex (card)", "")
    assert tab.claims_tab
    pane.close_secret(first, "✅ Stored")
    assert tab.claims_tab, "the second prompt takes the composer next"
    pane.close_secret(second, "✅ Stored")
    assert not tab.claims_tab


class FakeIntro:
    """Answers `logo_bottom` with a fixed row (None: not landed) and records the lift each
    `header` draw is asked for."""
    def __init__(self, bottom):
        self.bottom = bottom
        self.lifts = []

    def logo_bottom(self, screen):
        return self.bottom

    def header(self, screen, frame, lift=0):
        self.lifts.append(lift)
        return self.bottom


def test_the_chat_starts_under_the_logo_and_the_header_scrolls_away_with_the_transcript():
    from interfaces.console.framework.TUIScreen import TUIScreen
    from interfaces.console.tabs.HomeTab import LOGO_MARGIN
    from interfaces.console.Theme import TOP_MARGIN
    screen = TUIScreen(80, 40)
    tab = HomeTab(FakeConsole(), ChatPane())
    assert tab.header_rows(screen) == 0, "no intro: the chat starts at the top margin"
    tab = HomeTab(FakeConsole(), ChatPane(), FakeIntro(None))
    assert tab.header_rows(screen) == 0, "before the landing the layout is the plain one"
    tab = HomeTab(FakeConsole(), ChatPane(), FakeIntro(bottom=2))
    assert tab.header_rows(screen) == 0, "a logo above the margin never lifts the chat"

    intro = FakeIntro(bottom=9)
    pane = ChatPane()
    tab = HomeTab(FakeConsole(), pane, intro)
    assert tab.header_rows(screen) == 9 + 1 + LOGO_MARGIN - TOP_MARGIN
    tab.draw(screen, 0)
    assert pane.header_rows == tab.header_rows(screen) and intro.lifts == [0], "an empty transcript leaves the header put"
    for i in range(60):
        pane.add_notice(f"line {i}")
    tab.draw(TUIScreen(80, 40), 1)
    assert intro.lifts[-1] == pane.header_rows, "a full transcript has scrolled the header all the way up"
