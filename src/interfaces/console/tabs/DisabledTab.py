from typing import FrozenSet, List, Optional, Tuple

from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import DisabledIntegration, IntegrationMonitor
from common.logging import trace
from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.TabView import TabView, SUB_CONTENT_TOP
from interfaces.console.widgets import Board, Tile
from interfaces.console.widgets.Board import SummaryTile, Table

NAME_WIDTH, TAGS_WIDTH = 22, 33   # the three tags together are 32 wide
STARTING = "integrations are still starting"
ALL_ENABLED = "everything is enabled"
HINT = "↑↓ scroll  To turn one on, ask Keep in the Home chat"


class DisabledTab(TabView):
    """The last Integrations sub-tab: every integration that is switched off, with what it is.

    Read from the IntegrationMonitor's disabled placeholders, so it lists the ones without a
    tab of their own too (and the voice engines and the Telegram bot when their interface
    registers them). Read-only: enabling is the assistant's `enable_integration`."""

    def __init__(self, tab_integrations: FrozenSet[type] = frozenset()):
        """`tab_integrations`: the integration classes that bring a sub-tab once enabled."""
        self._tab_integrations = tab_integrations
        self._cards: List[Tile.Card] = []
        self._top = 0
        self._listed: Optional[Tuple[str, ...]] = None
        self._rows: list = []

    @property
    def name(self) -> str:
        return "Disabled"

    def _tags(self, placeholder: DisabledIntegration) -> str:
        tags = []
        if placeholder.is_feed:
            tags.append("feed")
        if placeholder.cls.sensitive:
            tags.append("private data")
        if placeholder.cls in self._tab_integrations:
            tags.append("adds a tab")
        return " · ".join(tags)

    def _table_rows(self, disabled: List[DisabledIntegration]) -> list:
        """A row per disabled integration, by name; rebuilt only when the list changed."""
        listed = tuple(sorted((p.name for p in disabled), key=str.lower))
        if listed != self._listed:
            trace(f"Disabled tab: listing {len(listed)} disabled integrations")
            self._listed = listed
            self._rows = [(Theme.COLOR_FAINT, [(p.name, Theme.COLOR_TEXT), (self._tags(p), Theme.COLOR_TEAL),
                                               (p.cls.description(), Theme.COLOR_LABEL)])
                          for p in sorted(disabled, key=lambda p: p.name.lower())]
        return self._rows

    def draw(self, screen: TUIScreen, frame: int):
        monitor = IntegrationMonitor.get_instance()
        registered = monitor.of_type(Integration)
        disabled = [i for i in registered if isinstance(i, DisabledIntegration)]
        rows = self._table_rows(disabled)

        self._cards = []
        available = screen.width - Board.LEFT - Board.RIGHT_MARGIN
        bottom = screen.height - 1                # the last row is the key hints'
        y = SUB_CONTENT_TOP
        if bottom - y - Board.SUMMARY_H - 1 > Tile.CARD_CHROME + 1:
            Board.draw_summary(screen, self._cards, y, available, self._tiles(len(registered), len(disabled)))
            y += Board.SUMMARY_H + 1
        table = Table("Disabled integrations",
                      [("Integration", NAME_WIDTH), ("Notes", TAGS_WIDTH), ("What it is", None)],
                      rows, ALL_ENABLED if registered else STARTING)
        self._top = Board.draw_table(screen, self._cards, Board.LEFT, y, available, bottom - y, table, self._top)
        Tile.tint_cards(screen, self._cards)
        Board.draw_hints(screen, HINT + (f': "enable {self._listed[0]}"' if self._listed else ""))

    @staticmethod
    def _tiles(registered: int, disabled: int) -> List[SummaryTile]:
        if not registered:
            return [("Disabled", "–", Theme.COLOR_DIM, [(STARTING, Theme.COLOR_DIM)])]
        return [("Disabled", str(disabled), Theme.COLOR_BRIGHT if disabled else Theme.COLOR_DIM,
                 [(f"of {registered} integrations", Theme.COLOR_LABEL)]),
                ("Enabled", str(registered - disabled), Theme.COLOR_GREEN,
                 [("each is on the Dashboard, some bring a tab here", Theme.COLOR_LABEL)])]

    def handle_input(self, key: str) -> bool:
        if key in ('up', 'down'):
            self._top = max(0, self._top + (1 if key == 'down' else -1))
            return True
        return False
