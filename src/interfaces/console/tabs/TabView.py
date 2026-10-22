from abc import ABC, abstractmethod
from interfaces.console.framework.TUIScreen import TUIScreen

# The first content row: under the two-row top tab bar and a blank row for a top-level tab,
# and under a TabGroup's spacer row and sub-tab bar as well for a sub-tab.
CONTENT_TOP = 4
SUB_CONTENT_TOP = 6


class TabView(ABC):
    """Base class for tab views in the TUI."""

    @property
    def name(self) -> str:
        """Label shown in the tab bar; derived from the class name unless overridden."""
        return self.__class__.__name__.removesuffix("Tab")

    @property
    def captures_text(self) -> bool:
        """True while the tab is taking typed text (a search field, the chat composer): every
        key goes to it, including the '/' and '|' tab-switching keys."""
        return False

    @property
    def claims_tab(self) -> bool:
        """True while the tab needs the Tab key itself (revealing a masked field); otherwise
        Tab and Shift+Tab walk through every tab, past a text field too."""
        return False

    def content_tab(self) -> "TabView":
        """The tab whose content is on screen; a TabGroup answers with its active sub-tab."""
        return self

    def paste_text(self, text: str) -> bool:
        """A pasted text, line breaks included, taken whole by a tab with a composer for it;
        False and the console types it key by key instead."""
        return False

    @abstractmethod
    def draw(self, screen: TUIScreen, frame: int):
        """Draw the tab content to the screen"""
        pass

    @abstractmethod
    def handle_input(self, key: str) -> bool:
        """
        Handle keyboard input specific to this tab.
        Returns True if the input was handled, False otherwise.
        """
        pass

    def on_activate(self):
        """Called when this tab becomes active"""
        pass

    def on_deactivate(self):
        """Called when this tab becomes inactive"""
        pass
