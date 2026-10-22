from typing import List

from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.TabBar import draw_tab_bar, fill_row, SUB_BAR
from interfaces.console.tabs.TabView import TabView
from common.logging import fine

SPACER_ROW = 3   # blank row in the sub-tab color joining the selected top tab to its sub-tabs
SUB_BAR_ROW = 4


class TabGroup(TabView):
    """A top-level tab whose content is one of several sub-tabs, picked from a second tab bar
    drawn right under the main one. `focused` says whether Shift+Left/Right currently walk
    the sub-tabs (True) or the top-level tabs (False); the TabNavigator flips it, and the
    Tab walk sets it whenever it lands on a sub-tab."""

    def __init__(self, name: str, tabs: List[TabView]):
        self._name = name
        self.tabs = tabs
        self.index = 0
        self.focused = False

    @property
    def name(self) -> str:
        return self._name

    def content_tab(self) -> TabView:
        return self.tabs[self.index]

    def enter(self, from_end: bool):
        """The Tab walk is about to land on this group: pick the sub-tab at the end it comes from."""
        self.index = len(self.tabs) - 1 if from_end else 0

    def select(self, delta: int):
        old = self.content_tab()
        old.on_deactivate()
        self.index = (self.index + delta) % len(self.tabs)
        new = self.content_tab()
        new.on_activate()
        fine(f"TUI sub-tab {old.name} -> {new.name} in {self.name}.")

    def draw(self, screen: TUIScreen, frame: int):
        fill_row(screen, SPACER_ROW, SUB_BAR.fill)
        draw_tab_bar(screen, SUB_BAR_ROW, [t.name for t in self.tabs], self.index, self.focused, SUB_BAR, indent=2)
        self.content_tab().draw(screen, frame)

    def handle_input(self, key: str) -> bool:
        return self.content_tab().handle_input(key)

    def paste_text(self, text: str) -> bool:
        return self.content_tab().paste_text(text)

    def on_activate(self):
        self.content_tab().on_activate()

    def on_deactivate(self):
        self.focused = False
        self.content_tab().on_deactivate()
