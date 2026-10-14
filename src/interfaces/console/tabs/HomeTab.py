from typing import TYPE_CHECKING, Optional
from common.logging import info
from .TabView import TabView
from interfaces.console import TUIState
from interfaces.console.Theme import LEFT_MARGIN, TOP_MARGIN
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.widgets.ChatPane import ChatPane
from interfaces.console.widgets.FeedStrip import FeedStrip
from assistant import FirstRun

if TYPE_CHECKING:
    from interfaces.console.TUIFloxyConsole import TUIFloxyConsole
    from interfaces.console.StartupIntro import StartupIntro

LOGO_MARGIN = 2   # blank rows between the logo's bottom and the chat


class HomeTab(TabView):
    """Main tab: the chat pane over the whole content area. It is the console's chat
    transport and, through `HistoryFeed`, the day's dialog history — every channel's turns
    and the system events in one transcript. The footer's right end is the feed strip
    (`FeedStrip`): a coloured dot per running feed, a one-cell progress bar with the ETA for
    one catching up; while the first-run pipeline builds the
    first memory bank a progress bar sits centred between the key hints and the strip
    (`FirstRun.curation_progress`). The live STT phrases are on
    Developer ▸ Voice. Ctrl+D toggles the "show debug" setting (persisted): the model's
    thinking block and the fallback notice show only when it is on.

    Once the startup intro has landed the Crystal object as the logo, the tab draws it and
    the title beside it where the intro left them (`StartupIntro.header`) and the chat
    starts `LOGO_MARGIN` rows under the logo's bottom row; until then, and without an
    intro, the chat starts at `TOP_MARGIN`. The rows the header takes are the pane's
    `header_rows`, so as the transcript fills the logo and title scroll up with it
    (`ChatPane.header_scroll`) and are not drawn once they are under the tab bar."""

    def __init__(self, console: 'TUIFloxyConsole', chat_pane: ChatPane, intro: 'Optional[StartupIntro]' = None):
        self.console = console
        self.chat_pane = chat_pane
        self.intro = intro
        self.feed_strip = FeedStrip()
        self.chat_pane.show_debug = bool(TUIState.load("home").get("show_debug", False))

    @property
    def captures_text(self) -> bool:
        return True

    @property
    def claims_tab(self) -> bool:
        return self.chat_pane.pending_secret is not None

    def draw(self, screen: TUIScreen, frame: int):
        self.chat_pane.footer_notice = "WAITING FOR USER" if self.console.waiting_for_user else None
        self.chat_pane.footer_chips = self.feed_strip.chips()
        curation = FirstRun.curation_progress()
        self.chat_pane.footer_progress = curation.fraction() if curation else None
        self.chat_pane.footer_eta = curation.eta_seconds() if curation else None
        self.chat_pane.header_rows = self.header_rows(screen)
        self.chat_pane.draw(screen, frame, LEFT_MARGIN, TOP_MARGIN,
                            screen.width - LEFT_MARGIN - 1, screen.height - TOP_MARGIN + 1)
        if self.intro is not None:
            self.intro.header(screen, frame, lift=self.chat_pane.header_scroll)

    def header_rows(self, screen: TUIScreen) -> int:
        """The rows the logo and its margin take off the top of the chat: none without a
        landed intro."""
        logo_bottom = self.intro.logo_bottom(screen) if self.intro is not None else None
        if logo_bottom is None:
            return 0
        return max(0, logo_bottom + 1 + LOGO_MARGIN - TOP_MARGIN)

    def handle_input(self, key: str) -> bool:
        """Ctrl+D toggles show debug; everything else is the chat pane's."""
        if key == '\x04':  # Ctrl+D - toggle show debug
            self.chat_pane.show_debug = not self.chat_pane.show_debug
            info(f"Home tab: show debug turned {'on' if self.chat_pane.show_debug else 'off'}")
            TUIState.save("home", show_debug=self.chat_pane.show_debug)
            return True
        return self.chat_pane.handle_input(key)
