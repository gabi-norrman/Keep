"""The Pool tab: the Aseko units' readings as a board — water, pH, chlorine and the unit in
summary tiles, every reading in a facts tile per unit, and the units' own messages in a table
under them. It shows what the prompt section last fetched; Ctrl-R fetches anew."""
import threading
import time
from typing import List

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.IntegrationTab import IntegrationTab
from interfaces.console.tabs.TabView import SUB_CONTENT_TOP
from interfaces.console.widgets import Board, Tile
from interfaces.console.widgets.Board import Fact, SummaryTile, Table
from integrations.aseko.AsekoIntegration import AsekoIntegration, number, readings, unit_name, warnings

HINTS = "Ctrl-R refresh"
NONE = "—"
SEVERITY_COLORS = {"ERROR": Theme.COLOR_RED, "WARNING": Theme.COLOR_YELLOW, "INFO": Theme.COLOR_LABEL}
PH_TOLERANCE = 0.2          # how far pH may sit from its target before its tile turns yellow
MESSAGES_MIN_ROWS = 3       # the messages table keeps this many rows; the facts tiles yield above it
LABEL_W = 15                # "Bound chlorine" is the widest label


class AsekoTab(IntegrationTab):
    """The pool's readings and the unit's messages, from the last fetch; Ctrl-R fetches anew."""

    integration = AsekoIntegration

    @property
    def name(self) -> str:
        return "Pool"

    def __init__(self):
        self._units: List[dict] = []
        self._message = ""
        self._loading = False
        self._cards: List[Tile.Card] = []

    def on_activate(self):
        api = self.integration.peek_instance()
        if api is not None and api.last_units:
            self._units = api.last_units
        elif not self._units:
            self._refresh()

    def _refresh(self):
        api = self.integration.peek_instance()
        if api is None:
            self._message = "Aseko Pool is not running"
            return
        if self._loading:
            return
        self._loading = True
        self._message = "Fetching..."

        def run():
            try:
                self._units = api.fetch_units()
                self._message = f"Fetched {time.strftime('%H:%M:%S')}"
            except Exception as e:
                self._message = f"Error: {e}"
            finally:
                self._loading = False

        self._run(run)

    @staticmethod
    def _run(fn):
        threading.Thread(target=fn, daemon=True).start()

    def handle_input(self, key: str) -> bool:
        if key == '\x12':   # Ctrl-R
            self._refresh()
            return True
        return False

    def draw(self, screen: TUIScreen, frame: int):
        self._cards = []
        available = screen.width - Board.LEFT - Board.RIGHT_MARGIN
        bottom = screen.height - 1                # the last row is the key hints'
        y = SUB_CONTENT_TOP
        Board.draw_summary(screen, self._cards, y, available, self._tiles())
        y += Board.SUMMARY_H + 1
        units = self._units
        if not units:
            Board.draw_notice(screen, self._cards, Board.LEFT, y, available, bottom - y, "Readings",
                              self._message or "No pool unit fetched yet")
        else:
            facts_per_unit = [self._facts(unit) for unit in units]
            table_min = Tile.CARD_CHROME + 1 + MESSAGES_MIN_ROWS
            facts_h = min(max(len(f) for f in facts_per_unit) + Tile.CARD_CHROME,
                          max(Tile.CARD_CHROME + 1, bottom - y - 1 - table_min))
            for (x, width), unit, facts in zip(Board.columns(available, len(units)), units, facts_per_unit):
                online = unit.get("online")
                Tile.draw_card(screen, self._cards, x, y, width, facts_h, unit_name(unit),
                               "online" if online else "offline", Theme.COLOR_GREEN if online else Theme.COLOR_RED)
                Board.draw_facts(screen, x, y, width, facts_h, facts, LABEL_W)
            y += facts_h + 1
            if bottom - y > Tile.CARD_CHROME:
                Board.draw_table(screen, self._cards, Board.LEFT, y, available, bottom - y, self._table(units))
        Tile.tint_cards(screen, self._cards)
        Board.draw_hints(screen, HINTS + (f"   {self._message}" if self._message else ""))

    # --- summary tiles -----------------------------------------------------------------------

    def _tiles(self) -> List[SummaryTile]:
        if not self._units:
            return [("Pool", NONE, Theme.COLOR_DIM, [(self._message or "no unit fetched yet", Theme.COLOR_DIM)])]
        unit = self._units[0]
        v = {k: x for k, x in (unit.get("statusValues") or {}).items() if x is not None}
        return [self._water_tile(v), self._ph_tile(v), self._chlorine_tile(v), self._unit_tile(unit)]

    @staticmethod
    def _target(v: dict, key: str, unit: str) -> List:
        return [(f"target {number(v[key])}{unit}", Theme.COLOR_LABEL)] if key in v else []

    def _water_tile(self, v: dict) -> SummaryTile:
        if "waterTemperature" not in v:
            return "Water", NONE, Theme.COLOR_DIM, [("no reading", Theme.COLOR_DIM)]
        return "Water", f"{number(v['waterTemperature'])}°C", Theme.COLOR_CYAN, self._target(v, "waterTemperatureRequired", "°C")

    def _ph_tile(self, v: dict) -> SummaryTile:
        if "ph" not in v:
            return "pH", NONE, Theme.COLOR_DIM, [("no reading", Theme.COLOR_DIM)]
        color = Theme.COLOR_CYAN
        if "phRequired" in v:
            color = Theme.COLOR_GREEN if abs(v["ph"] - v["phRequired"]) <= PH_TOLERANCE else Theme.COLOR_YELLOW
        return "pH", number(v["ph"]), color, self._target(v, "phRequired", "")

    def _chlorine_tile(self, v: dict) -> SummaryTile:
        if "clFree" in v:
            unit = f" {v.get('clFreeRequiredUnit') or 'mg/l'}"
            return "Free chlorine", f"{number(v['clFree'])}{unit}", Theme.COLOR_CYAN, self._target(v, "clFreeRequired", unit)
        if "redox" in v:
            return "Redox", f"{number(v['redox'])} mV", Theme.COLOR_CYAN, self._target(v, "redoxRequired", " mV")
        return "Chlorine", NONE, Theme.COLOR_DIM, [("no reading", Theme.COLOR_DIM)]

    @staticmethod
    def _unit_tile(unit: dict) -> SummaryTile:
        online = unit.get("online")
        segments = []
        mode = (unit.get("statusValues") or {}).get("mode")
        if mode:
            segments.append((f"mode {mode}", Theme.COLOR_LABEL))
        messages = warnings(unit)
        if messages:
            worst = messages[0].get("severity", "INFO")
            segments.append((f"{len(messages)} message{'s' if len(messages) != 1 else ''}", SEVERITY_COLORS.get(worst, Theme.COLOR_LABEL)))
        else:
            segments.append(("no messages", Theme.COLOR_DIM))
        return ("Unit", "online" if online else "offline", Theme.COLOR_GREEN if online else Theme.COLOR_RED, segments)

    # --- readings and messages ---------------------------------------------------------------

    @staticmethod
    def _facts(unit: dict) -> List[Fact]:
        values = unit.get("statusValues")
        if values is None:
            return [Fact("Readings", "never connected to Aseko Cloud", Theme.COLOR_DIM)]
        facts = [Fact(label, text, Theme.COLOR_RED if label == "Probes" or "alarm" in text else Theme.COLOR_TEXT)
                 for label, text in readings(values)]
        return facts or [Fact("Readings", "none reported", Theme.COLOR_DIM)]

    @staticmethod
    def _table(units: List[dict]) -> Table:
        many = len(units) > 1
        columns = ([("Unit", 16)] if many else []) + [("Severity", 8), ("Message", 44), ("Detail", None)]
        rows = []
        for unit in units:
            for m in warnings(unit):
                color = SEVERITY_COLORS.get(m.get("severity"), Theme.COLOR_LABEL)
                cells = ([(unit_name(unit), Theme.COLOR_LABEL)] if many else []) + [
                    (m.get("severity") or "INFO", color), (m.get("message") or m.get("type") or "", Theme.COLOR_TEXT),
                    (m.get("detail") or "", Theme.COLOR_DIM)]
                rows.append((color, cells))
        return Table("Unit messages", columns, rows, "none — the unit reports nothing wrong", empty_color=Theme.COLOR_GREEN)
