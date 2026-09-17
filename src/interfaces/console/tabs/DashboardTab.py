import collections
import math
import textwrap
import time
from typing import TYPE_CHECKING, Callable, Dict, List, NamedTuple, Optional, Tuple

from .TabView import TabView, CONTENT_TOP
from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import display_width, fit
from interfaces.console.widgets.FeedStrip import SHORT_NAMES, SYMBOL_FAILED, SYMBOL_PAUSED, SYMBOL_RUNNING, format_eta
from common.GpuMonitor import GpuMonitor
from common.AnthropicCostMonitor import AnthropicCostMonitor
from common.MemoryLogMonitor import MemoryLogMonitor
from common.TempMonitor import TempMonitor
from common.TTFTMonitor import TTFTMonitor
from common.STTConfidenceMonitor import STTConfidenceMonitor
from assistant.integration.Integration import IntegrationState
from assistant.integration.FeedRunner import Feeds, FeedState
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from assistant.llm.OllamaLLM import OllamaLLM

if TYPE_CHECKING:
    from interfaces.console.TUIFloxyConsole import TUIFloxyConsole


class FeedRow(NamedTuple):
    """One line of the Feeds panel, worked out from a FeedRunner status dict."""
    symbol: str
    name: str
    fg: Color
    bg: Color
    progress: Optional[float]   # 0..1 bar fill; None = catching up without a count (sweeping notch)
    text: str                   # "1234 / 23356 photos ETA: 13h" through a catch-up, "23436 photos" live, else the state


class DashboardTab(TabView):
    """Tab charting GPU, CPU, temperature, memory, TTFT and API cost over time, with a status
    panel underneath: integration health, last-request timing, resource bars and prompt sections."""

    COLOR_PANEL_BG = Color(20, 22, 30)
    COLOR_BORDER = Color(40, 42, 54)
    COLOR_BRIGHT = Color(220, 225, 235)
    COLOR_DIM = Color(100, 105, 115)
    COLOR_LABEL = Color(140, 145, 155)

    COLOR_CYAN = Color(0, 200, 255)
    COLOR_CYAN_DIM = Color(0, 50, 64)
    COLOR_GREEN = Color(0, 220, 120)
    COLOR_GREEN_DIM = Color(0, 55, 30)
    COLOR_YELLOW = Color(255, 200, 0)
    COLOR_YELLOW_DIM = Color(64, 50, 0)
    COLOR_PINK = Color(255, 100, 180)
    COLOR_PINK_DIM = Color(64, 25, 45)
    COLOR_PURPLE = Color(170, 100, 255)
    COLOR_RED = Color(255, 90, 90)
    COLOR_RED_DIM = Color(64, 22, 22)

    HISTORY_SIZE = 120
    BLOCK_CHARS = [' ', '\u2581', '\u2582', '\u2583', '\u2584', '\u2585', '\u2586', '\u2587', '\u2588']

    def __init__(self, console: 'TUIFloxyConsole'):
        self.console = console
        self._frame = 0

        self.device_util = 0
        self.tiler_util = 0
        self.renderer_util = 0
        self.alloc_memory = 0
        self.in_use_memory = 0
        self.in_use_memory_driver = 0
        self.pb_size = 0
        self.tiled_scene_bytes = 0
        self.recovery_count = 0

        self.model = ""
        self.gpu_core_count = 0
        self.cpu_brand = ""
        self.p_cores = 0
        self.e_cores = 0
        self.total_memory_gb = 0

        self.device_history: collections.deque = collections.deque(maxlen=self.HISTORY_SIZE)
        self.tiler_history: collections.deque = collections.deque(maxlen=self.HISTORY_SIZE)
        self.renderer_history: collections.deque = collections.deque(maxlen=self.HISTORY_SIZE)
        self.memory_history: collections.deque = collections.deque(maxlen=self.HISTORY_SIZE)
        self.cpu_history: collections.deque = collections.deque(maxlen=self.HISTORY_SIZE)
        self._last_cpu_sample: float = 0.0

        # Anthropic cost data (written by AnthropicCostMonitor)
        self.daily_costs: dict = {}
        self.cost_models: list = []
        self.total_cost_30d: float = 0.0
        self.total_cost_7d: float = 0.0
        self.total_cost_1d: float = 0.0
        self.total_cost_yesterday: float = 0.0
        self.cost_dates: list = []
        self.cost_date_strs: list = []
        self.cost_error: str = ""
        self.cost_last_updated: float = 0.0

        self.memory_log_sizes: list = []
        self.memory_log_error: str = ""

        self.cpu_temp: float = 0.0
        self.gpu_temp: float = 0.0
        self.temp_error: str = ""
        self.temp_history: collections.deque = collections.deque(maxlen=self.HISTORY_SIZE)

        self._monitor = GpuMonitor(gpu_tab=self, update_interval=0.33)
        self._cost_monitor = AnthropicCostMonitor(dashboard_tab=self, fetch_interval=300.0)
        self._memory_log_monitor = MemoryLogMonitor(dashboard_tab=self, fetch_interval=300.0)
        self._temp_monitor = TempMonitor(dashboard_tab=self, update_interval=30.0)

    def draw(self, screen: TUIScreen, frame: int):
        self._frame = frame
        self._sample_cpu()
        y = CONTENT_TOP
        left = 3

        y = self._draw_header(screen, left, y)
        y += 1
        y = self._draw_summary_panel(screen, left, y)
        y += 1

        remaining = screen.height - y - 1
        # The status panel below the charts grows with the integration list and the
        # outposts, so the three chart rows (box = chart_height + 2, plus a gap row)
        # take whatever height is left after reserving it.
        panel_h = self._status_panel_height()
        chart_height = max(5, (remaining - panel_h) // 3 - 3)
        self._draw_charts_row(screen, left, y, chart_height)
        y += chart_height + 2 + 1
        self._draw_ttft_charts_row(screen, left, y, chart_height)
        y += chart_height + 2 + 1
        self._draw_bottom_charts_row(screen, left, y, chart_height)
        y += chart_height + 2 + 1
        self._draw_status_panel(screen, left, y)

    def handle_input(self, key: str) -> bool:
        return False

    def on_activate(self):
        self._monitor.start()
        self._cost_monitor.start()
        self._memory_log_monitor.start()
        self._temp_monitor.start()

    def on_deactivate(self):
        self._monitor.stop()
        self._cost_monitor.stop()
        self._memory_log_monitor.stop()
        self._temp_monitor.stop()

    def _sample_cpu(self):
        now = time.monotonic()
        if now - self._last_cpu_sample >= 1.0:
            self._last_cpu_sample = now
            self.cpu_history.append(self.console.system_cpu_percent)

    def _draw_bottom_charts_row(self, screen: TUIScreen, x: int, y: int, chart_h: int):
        available_w = screen.width - x - 2
        gap = 2
        third_w = (available_w - 2 * gap) // 3

        self._draw_long_term_memory_chart(screen, x, y, third_w, chart_h)

        self._draw_temp_chart(screen, x + third_w + gap, y, third_w, chart_h)

        cost_x = x + (third_w + gap) * 2
        self._draw_cost_chart(screen, cost_x, y, available_w - (third_w + gap) * 2, chart_h + 2)

    def _draw_header(self, screen: TUIScreen, x: int, y: int) -> int:
        if not self.model:
            screen.set_color(self.COLOR_DIM)
            screen.write_at(x, y, "Loading GPU data...")
            return y + 1

        screen.set_color(self.COLOR_CYAN)
        screen.write_at(x, y, "\u25cf")
        screen.set_color(self.COLOR_BRIGHT)
        screen.bold()
        screen.write_at(x + 2, y, self.model)
        screen.reset_color()

        y += 1
        mem = f"{self.total_memory_gb}GB Unified Memory" if self.total_memory_gb else "Unified Memory Architecture"
        specs = f"  {self.gpu_core_count}-core GPU \u00b7 {self.p_cores + self.e_cores}-core CPU \u00b7 {mem}"
        screen.set_color(self.COLOR_DIM)
        screen.write_at(x, y, specs)
        screen.reset_color()
        return y + 1

    def _draw_summary_panel(self, screen: TUIScreen, x: int, y: int) -> int:
        """Three equal boxes: utilization, mic noise floor, feeds; the last takes the rounding remainder."""
        available_w = screen.width - x - 3
        gap = 2
        panel_w = (available_w - 2 * gap) // 3
        panel_h = 6

        self._draw_utilization_panel(screen, x, y, panel_w, panel_h)
        self._draw_noise_floor_panel(screen, x + panel_w + gap, y, panel_w, panel_h)
        self._draw_feeds_panel(screen, x + (panel_w + gap) * 2, y,
                               available_w - (panel_w + gap) * 2, panel_h)
        return y + panel_h

    def _draw_utilization_panel(self, screen: TUIScreen, x: int, y: int, panel_w: int, panel_h: int):
        self._draw_chart_box(screen, x, y, panel_w, panel_h, "Utilization")

        bars = [
            ("Device", self.device_util, self.COLOR_CYAN, self.COLOR_CYAN_DIM),
            ("Tiler", self.tiler_util, self.COLOR_GREEN, self.COLOR_GREEN_DIM),
            ("Renderer", self.renderer_util, self.COLOR_YELLOW, self.COLOR_YELLOW_DIM),
            ("CPU", int(self.console.system_cpu_percent), self.COLOR_PINK, self.COLOR_PINK_DIM),
        ]

        label_w = max(len(label) for label, *_ in bars)
        bar_x = x + 3 + label_w + 1
        bar_len = panel_w - (bar_x - x) - 7

        for i, (label, value, fg, bg) in enumerate(bars):
            row_y = y + 1 + i
            screen.set_color(self.COLOR_LABEL)
            screen.write_at(x + 3, row_y, f"{label:<{label_w}}")
            screen.draw_progress_bar(bar_x, row_y, bar_len, value / 100.0, fg, bg)
            screen.set_color(fg)
            screen.bold()
            screen.write_at(bar_x + bar_len + 2, row_y, f"{value}%")
            screen.reset_color()

    # Fixed full-scale for the noise panel bar (raw frame RMS). Both values render on
    # this one scale: the fill is the live 1s low-percentile (the raise rule's input),
    # the bright notch marks the current noise floor.
    NOISE_BAR_MAX_RMS = 0.05

    def _draw_noise_floor_panel(self, screen: TUIScreen, x: int, y: int, panel_w: int, panel_h: int):
        """Per-mic view of the adaptive energy gate (see RmsVAD): the live level as
        the bar fill, the floor as a marker notch, both on a fixed 0..NOISE_BAR_MAX_RMS
        scale. Fill red when the level sits at/above the floor (the raise rule is
        lifting it), green otherwise. Values are raw RMS matching the log lines."""
        self._draw_chart_box(screen, x, y, panel_w, panel_h, "Mic noise floor")

        floors = self._mic_noise_floors()
        if not floors:
            screen.set_color(self.COLOR_DIM)
            screen.write_at(x + 3, y + 1, "no local outposts")
            screen.reset_color()
            return

        label_w = max(len(label) for label, *_ in floors)
        bar_x = x + 3 + label_w + 1
        # value area: " 0.0004 (floor 0.0008)" ≈ 22 cols
        bar_len = panel_w - (bar_x - x) - 24

        for i, (label, floor, _threshold, live_min) in enumerate(floors[:panel_h - 2]):
            row_y = y + 1 + i
            screen.set_color(self.COLOR_LABEL)
            screen.write_at(x + 3, row_y, f"{label:<{label_w}}")
            if floor is None or live_min is None:
                screen.set_color(self.COLOR_DIM)
                screen.write_at(bar_x, row_y, "calibrating…")
                continue
            rising = live_min >= floor
            fg, bg = (self.COLOR_RED, self.COLOR_RED_DIM) if rising \
                else (self.COLOR_GREEN, self.COLOR_GREEN_DIM)
            if bar_len > 0:
                screen.draw_progress_bar(bar_x, row_y, bar_len,
                                         live_min / self.NOISE_BAR_MAX_RMS, fg, bg,
                                         marker=floor / self.NOISE_BAR_MAX_RMS)
            screen.set_color(fg)
            screen.bold()
            value = f"{live_min:.4f}"
            screen.write_at(bar_x + bar_len + 2, row_y, value)
            screen.reset_color()
            screen.set_color(self.COLOR_DIM)
            screen.write_at(bar_x + bar_len + 2 + len(value) + 1, row_y,
                            f"(floor {floor:.4f})")
            screen.reset_color()

    @staticmethod
    def _mic_noise_floors():
        # Imported lazily: the outpost stack is heavyweight and absent in some
        # deployments; the console must render regardless.
        try:
            from interfaces.voice.microphones.local_outposts.LocalOutpostManager import LocalOutpostManager
        except ImportError:
            return []
        manager = LocalOutpostManager.instance
        return manager.noise_floors() if manager is not None else []

    # --- Feeds panel ---

    FEED_MIN_BAR = 6
    FEED_SWEEP_FRAMES = 90      # one bounce of the indeterminate notch: 3 s at 30 fps
    SYMBOL_CATCHING_UP = "◐"    # the FeedStrip draws a bar for a catch-up; the panel has one of its own

    def _draw_feeds_panel(self, screen: TUIScreen, x: int, y: int, panel_w: int, panel_h: int):
        """One row per running feed: the name, a state symbol (the FeedStrip's dots and cross, a
        half dot for a catch-up), a bar in the state's
        colour (green live, yellow catching up, red stopped or failed, grey starting; paused fades
        it) and, in that same colour, the feed's rows in the history with its item noun —
        "1234 / 23356 photos ETA: 13h" through a catch-up (the total is what it will hold once
        caught up), "23436 messages" once live — or the state word when paused, stopped or
        failed. A feed catching up without a count gets a notch sweeping the empty bar instead."""
        self._draw_chart_box(screen, x, y, panel_w, panel_h, "Feeds")

        rows = [self._feed_row(name, status) for name, status in self._feed_statuses()]
        if not rows:
            screen.set_color(self.COLOR_DIM)
            screen.write_at(x + 3, y + 1, "no feeds running")
            screen.reset_color()
            return

        rows = rows[:panel_h - 2]
        label_w = max(len(row.name) for row in rows)
        symbol_x = x + 3 + label_w + 1
        bar_x = symbol_x + 2
        # The bar takes every cell the longest text on screen leaves it.
        text_w = max(display_width(row.text) for row in rows)
        bar_len = max(self.FEED_MIN_BAR, panel_w - (bar_x - x) - text_w - 4)
        text_x = bar_x + bar_len + 2
        text_w = x + panel_w - 2 - text_x     # one cell of air before the right border

        for i, row in enumerate(rows):
            row_y = y + 1 + i
            screen.set_color(self.COLOR_LABEL)
            screen.write_at(x + 3, row_y, f"{row.name:<{label_w}}")
            screen.set_color(row.fg)
            screen.write_at(symbol_x, row_y, row.symbol)
            if row.progress is None:
                screen.draw_progress_bar(bar_x, row_y, bar_len, 0.0, row.fg, row.bg,
                                         marker=self._sweep_position(), marker_color=row.fg)
            else:
                screen.draw_progress_bar(bar_x, row_y, bar_len, row.progress, row.fg, row.bg)
            if text_w > 0:
                screen.set_color(row.fg)
                screen.bold()
                screen.write_at(text_x, row_y, fit(row.text, text_w))
                screen.reset_color()

    def _sweep_position(self) -> float:
        """0..1..0 triangle wave over FEED_SWEEP_FRAMES frames."""
        t = (self._frame % self.FEED_SWEEP_FRAMES) / self.FEED_SWEEP_FRAMES
        return 2 * t if t < 0.5 else 2 - 2 * t

    @staticmethod
    def _feed_statuses() -> List[Tuple[str, dict]]:
        feeds = Feeds.get_instance()
        statuses = []
        for name in feeds.names():
            status = feeds.status(name)
            if status is not None:
                statuses.append((name, status))
        return statuses

    @classmethod
    def _feed_row(cls, name: str, status: dict) -> FeedRow:
        state = status["state"]
        items = status.get("items", "items")
        progress: Optional[float] = 0.0
        if state == FeedState.LIVE.value:
            symbol, fg, bg = SYMBOL_RUNNING, cls.COLOR_GREEN, cls.COLOR_GREEN_DIM
            progress = 1.0
            text = f"{status['stored']} {items}"
        elif state == FeedState.CATCHING_UP.value:
            symbol, fg, bg = cls.SYMBOL_CATCHING_UP, cls.COLOR_YELLOW, cls.COLOR_YELLOW_DIM
            progress = status.get("progress")
            stored, total = status["stored"], status.get("total")
            text = f"{stored} {items}" if total in (None, stored) else f"{stored} / {total} {items}"
            if status.get("eta_seconds") is not None:
                text += f" ETA: {format_eta(status['eta_seconds'])}"
        elif state in (FeedState.STOPPED.value, FeedState.FAILED.value):
            symbol, fg, bg = SYMBOL_FAILED, cls.COLOR_RED, cls.COLOR_RED_DIM
            text = state
        else:
            symbol, fg, bg = SYMBOL_RUNNING, cls.COLOR_DIM, cls.COLOR_BORDER
            text = state
        if status.get("paused") and symbol != SYMBOL_FAILED:
            symbol = SYMBOL_PAUSED
            fg = cls._lerp_color(cls.COLOR_PANEL_BG, fg, 0.5)
            text = "paused"
        return FeedRow(symbol, SHORT_NAMES.get(name, name), fg, bg, progress, text)

    def _draw_charts_row(self, screen: TUIScreen, x: int, y: int, chart_h: int):
        available_w = screen.width - x - 2
        gap = 2
        chart_w = (available_w - 2 * gap) // 3

        self._draw_bar_chart(screen, x, y, chart_w, chart_h,
                             self.device_history, Theme.COLOR_GREEN_FG_BRIGHTER,
                             "GPU Usage", f"{self.device_util}%")

        self._draw_bar_chart(screen, x + chart_w + gap, y, chart_w, chart_h,
                             self.memory_history, Theme.COLOR_BLUE_FG_BRIGHTER,
                             "Memory Usage", self._format_bytes(self.in_use_memory))

        self._draw_bar_chart(screen, x + (chart_w + gap) * 2, y, chart_w, chart_h,
                             self.cpu_history, Theme.COLOR_YELLOW_FG,
                             "CPU Usage", f"{self.console.system_cpu_percent:.0f}%")

    LTM_MIN_MAX_KB = 10.0

    def _draw_long_term_memory_chart(self, screen: TUIScreen, x: int, y: int, width: int, chart_h: int):
        sizes = [kb for _, kb in self.memory_log_sizes]
        latest_str = f"{sizes[-1]:.0f} KB" if sizes else ""
        peak = max(sizes) if sizes else 0.0
        max_kb = self._nice_ceil(peak) if peak > self.LTM_MIN_MAX_KB else self.LTM_MIN_MAX_KB

        self._draw_bar_chart(screen, x, y, width, chart_h,
                             sizes, self.COLOR_PINK,
                             "Long Term Memory", latest_str,
                             max_value=max_kb, y_axis_format=self._format_kb)

    @staticmethod
    def _format_kb(v: float) -> str:
        return f"{v:.0f}KB"

    # Apple Silicon junction temps: throttling starts ~100-105°C, so sustained
    # readings near 95°C mean the warm room is eating all thermal headroom.
    TEMP_WARN_C = 75.0
    TEMP_BAD_C = 95.0
    TEMP_SCALE_MAX_C = 110.0

    def _temp_zone(self, temp_c: float):
        if temp_c >= self.TEMP_BAD_C:
            return self.COLOR_RED, self.COLOR_RED_DIM, "BAD"
        if temp_c >= self.TEMP_WARN_C:
            return self.COLOR_YELLOW, self.COLOR_YELLOW_DIM, "WARM"
        return self.COLOR_GREEN, self.COLOR_GREEN_DIM, "OK"

    def _draw_temp_chart(self, screen: TUIScreen, x: int, y: int, width: int, chart_h: int):
        """The bar chart with a colour per temperature zone and dashed guides at the zone thresholds."""
        fg, _, status = self._temp_zone(self.cpu_temp)
        value = "" if self.cpu_temp <= 0 else (f"{self.cpu_temp:.0f}°C" if status == "OK" else f"{self.cpu_temp:.0f}°C {status}")
        self._draw_chart_box(screen, x, y, width, chart_h + 2, "CPU Temp", value, fg)

        chart_y = y + 1
        chart_x = x + 2 + self.Y_AXIS_W
        chart_w = width - 4 - self.Y_AXIS_W
        if chart_w <= 0 or chart_h <= 0:
            return

        if self.temp_error and not self.temp_history:
            screen.set_color(self.COLOR_DIM)
            screen.write_at(x + 3, chart_y + 1, self.temp_error[:width - 6])
            screen.reset_color()
            return

        max_value = self.TEMP_SCALE_MAX_C
        warn_row = min(chart_h - 1, int((self.TEMP_WARN_C / max_value) * chart_h))
        bad_row = min(chart_h - 1, int((self.TEMP_BAD_C / max_value) * chart_h))
        warn_guide = self._lerp_color(self.COLOR_PANEL_BG, self.COLOR_YELLOW, 0.45)
        bad_guide = self._lerp_color(self.COLOR_PANEL_BG, self.COLOR_RED, 0.45)

        # Y-axis: label the warning/bad thresholds in their zone colors
        self._fill_y_axis(screen, x + 2, chart_y, chart_h, self.Y_AXIS_W)
        for label_val, label_color, label_row in (
                (self.TEMP_WARN_C, warn_guide, warn_row),
                (self.TEMP_BAD_C, bad_guide, bad_row)):
            screen.set_color(label_color)
            screen.set_bg_color(self.COLOR_PANEL_BG)
            screen.write_at(x + 2, chart_y + chart_h - 1 - label_row,
                            f"{label_val:.0f}°C".rjust(self.Y_AXIS_W))

        # The bad guide wins the row when both thresholds land on it
        self._draw_columns(screen, chart_x, chart_y, chart_w, chart_h, self.temp_history, max_value,
                           lambda v: self._temp_zone(v)[0], guides={warn_row: warn_guide, bad_row: bad_guide})

    Y_AXIS_W = 5

    def _draw_chart_box(self, screen: TUIScreen, x: int, y: int, width: int, height: int, label: str,
                        value: str = "", value_color: Optional[Color] = None):
        """A rounded panel with its label in the top border and, when given, a bold `value`
        at the border's right end."""
        screen.draw_box(x, y, width, height, style="rounded", color=self.COLOR_BORDER)
        screen.set_color(self.COLOR_DIM)
        screen.write_at(x + 2, y, f" {label} ")
        if value:
            screen.set_color(value_color)
            screen.bold()
            screen.write_at(x + width - len(value) - 3, y, f" {value} ")
            screen.reset_color()

    def _fill_y_axis(self, screen: TUIScreen, x: int, chart_y: int, chart_h: int, axis_w: int):
        """Paint the y-axis column in the panel background before its labels go on."""
        screen.set_bg_color(self.COLOR_PANEL_BG)
        screen.set_color(self.COLOR_PANEL_BG)
        for row in range(chart_h):
            screen.write_at(x, chart_y + row, ' ' * axis_w)

    def _draw_columns(self, screen: TUIScreen, chart_x: int, chart_y: int, chart_w: int, chart_h: int,
                      history, max_value: float, color_for: Callable[[float], Color],
                      guides: Optional[Dict[int, Color]] = None):
        """One column per value, the newest at the right, each bar fading up from a dim base
        to its full colour. `color_for(value)` gives a column its colour; `guides` maps a
        chart row to the colour of a dashed line drawn across the empty part of the chart.
        A full block carries its colour as background too: the glyph covers the cell in most
        terminals, but Terminal.app leaves seams between cells that would show the ground."""
        data = list(history)
        if len(data) > chart_w:
            data = data[-chart_w:]
        data = [0.0] * (chart_w - len(data)) + data
        guides = guides or {}
        dim: Dict[Color, Color] = {}
        max_row = max(1, chart_h - 1)

        for col_idx, value in enumerate(data):
            cx = chart_x + col_idx
            fg = color_for(value)
            if fg not in dim:
                dim[fg] = self._lerp_color(self.COLOR_PANEL_BG, fg, 0.35)
            dim_fg = dim[fg]
            filled_rows = (value / max_value) * chart_h if max_value > 0 else 0
            full_rows = int(filled_rows)
            partial = filled_rows - full_rows

            for row in range(chart_h):
                cell_y = chart_y + chart_h - 1 - row
                t = row / max_row
                if row < full_rows:
                    color = self._lerp_color(dim_fg, fg, t)
                    screen.set_color(color)
                    screen.set_bg_color(color)
                    screen.write_at(cx, cell_y, '█')
                elif row == full_rows and partial > 0:
                    screen.set_color(self._lerp_color(dim_fg, fg, t))
                    screen.set_bg_color(self.COLOR_PANEL_BG)
                    screen.write_at(cx, cell_y, self.BLOCK_CHARS[max(1, int(partial * 8))])
                elif row in guides:
                    screen.set_color(guides[row])
                    screen.set_bg_color(self.COLOR_PANEL_BG)
                    screen.write_at(cx, cell_y, '┄')
                else:
                    screen.set_bg_color(self.COLOR_PANEL_BG)
                    screen.set_color(self.COLOR_PANEL_BG)
                    screen.write_at(cx, cell_y, ' ')

        screen.reset_color()

    def _draw_bar_chart(self, screen: TUIScreen, x: int, y: int, width: int, chart_h: int,
                        history: collections.deque, fg: Color,
                        label: str, value_str: str,
                        max_value: float = 100.0, y_axis_format=None):
        self._draw_chart_box(screen, x, y, width, chart_h + 2, label, value_str, fg)

        chart_y = y + 1
        if y_axis_format:
            chart_x = x + 2 + self.Y_AXIS_W
            chart_w = width - 4 - self.Y_AXIS_W
            self._fill_y_axis(screen, x + 2, chart_y, chart_h, self.Y_AXIS_W)
            for i in range(3):
                lv = max_value * i / 2
                ly = chart_y + chart_h - 1 - int((i / 2) * (chart_h - 1))
                screen.set_color(self.COLOR_DIM)
                screen.set_bg_color(self.COLOR_PANEL_BG)
                screen.write_at(x + 2, ly, y_axis_format(lv).rjust(self.Y_AXIS_W))
        else:
            chart_x = x + 2
            chart_w = width - 4

        if chart_w <= 0 or chart_h <= 0:
            return
        self._draw_columns(screen, chart_x, chart_y, chart_w, chart_h, history, max_value, lambda _: fg)

    @staticmethod
    def _format_seconds(v: float) -> str:
        return f"{v:.1f}s" if v < 10 else f"{v:.0f}s"

    @staticmethod
    def _format_pct(v: float) -> str:
        return f"{v:.0f}%"

    @staticmethod
    def _format_bytes(b: int) -> str:
        if b <= 0:
            return "0 B"
        if b >= 1024 ** 3:
            return f"{b / (1024 ** 3):.1f} GB"
        if b >= 1024 ** 2:
            return f"{b / (1024 ** 2):.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"

    # --- TTFT Charts ---

    DEFAULT_TTFT_MAX_SECONDS = 5.0

    def _draw_ttft_charts_row(self, screen: TUIScreen, x: int, y: int, chart_height: int):
        available_w = screen.width - x - 2
        gap = 2
        chart_w = (available_w - gap * 2) // 3

        monitor = TTFTMonitor.get_instance()

        smart_data = monitor.smart_ttft
        smart_label = f"Smart AI TTFT ({monitor.smart_model_short})" if monitor.smart_model_short else "Smart AI TTFT"
        smart_value = f"{smart_data[-1]:.2f}s" if smart_data else ""
        smart_max = self._ttft_max(smart_data)
        self._draw_bar_chart(screen, x, y, chart_w, chart_height,
                             smart_data, self.COLOR_CYAN,
                             smart_label, smart_value,
                             max_value=smart_max, y_axis_format=self._format_seconds)

        fast_data = monitor.fast_ttft
        fast_label = f"Fast AI TTFT ({monitor.fast_model_short})" if monitor.fast_model_short else "Fast AI TTFT"
        fast_value = f"{fast_data[-1]:.2f}s" if fast_data else ""
        fast_max = self._ttft_max(fast_data)
        self._draw_bar_chart(screen, x + chart_w + gap, y, chart_w, chart_height,
                             fast_data, self.COLOR_PURPLE,
                             fast_label, fast_value,
                             max_value=fast_max, y_axis_format=self._format_seconds)

        cache_data = monitor.cache_pct
        cache_value = f"{cache_data[-1]}%" if cache_data else ""
        self._draw_bar_chart(screen, x + (chart_w + gap) * 2, y, chart_w, chart_height,
                             cache_data, self.COLOR_GREEN,
                             "KV-Cache Hit %", cache_value,
                             max_value=100.0, y_axis_format=self._format_pct)

    def _ttft_max(self, data: collections.deque) -> float:
        max_val = max(data) if data else 0
        return self._nice_ceil(max_val) if max_val > self.DEFAULT_TTFT_MAX_SECONDS else self.DEFAULT_TTFT_MAX_SECONDS

    # --- Status panel (integrations, request timing, resource bars, prompt sections) ---

    STATUS_STATE_COLORS = {
        IntegrationState.OK.value: Color(58, 173, 130),
        IntegrationState.WARNING.value: Color(148, 124, 65),
        IntegrationState.NOT_CONFIGURED.value: Color(80, 80, 80),
        IntegrationState.ERROR.value: Color(160, 48, 46),
        IntegrationState.NEEDS_2FA.value: Color(160, 100, 200),
        IntegrationState.RATE_LIMITED.value: Color(200, 130, 50),
        IntegrationState.UNKNOWN.value: Color(80, 80, 80),
        IntegrationState.DISABLED.value: Color(80, 80, 80),
    }
    TIMING_ROWS = 7           # six stages + Total
    RESOURCE_ROWS = 12        # nine resource bars + Volume, Background noise, KV-Cache

    INTEGRATIONS_WIDTH = 38   # the integration column: from its bullets to the timing bars' labels

    @classmethod
    def _integration_rows(cls, statuses: List[dict]) -> Tuple[List[dict], List[str]]:
        """The enabled integrations, sorted by name, and the wrapped "Disabled: a, b, c" lines
        that follow them — a disabled integration only takes a share of one line, not a row."""
        enabled = sorted((s for s in statuses if s["state"] != IntegrationState.DISABLED.value),
                         key=lambda s: s["name"])
        disabled = sorted(s["name"] for s in statuses if s["state"] == IntegrationState.DISABLED.value)
        lines = textwrap.wrap("Disabled: " + ", ".join(disabled), cls.INTEGRATIONS_WIDTH) if disabled else []
        return enabled, lines

    def _status_panel_height(self) -> int:
        """Tallest of the four status columns, so the chart rows above can shrink to fit it."""
        enabled, disabled_lines = self._integration_rows(IntegrationMonitor.get_instance().get_all_statuses())
        integrations = len(enabled) + (1 + len(disabled_lines) if disabled_lines else 0)
        resources = self.RESOURCE_ROWS + len(self.console.outpost_latencies)
        pm = self.console.prompt_manager
        prompt_sections = len(pm.get_section_stats()) + 1 if pm else 0
        return max(integrations, self.TIMING_ROWS, resources, prompt_sections)

    def _draw_status_panel(self, screen: TUIScreen, x: int, y: int):
        self._draw_integrations(screen, x + 2, y)
        self._draw_timing(screen, x + 42, y)
        self._draw_resource_bars(screen, y)
        self._draw_prompt_sections(screen, y)

    def _draw_integrations(self, screen: TUIScreen, x: int, y: int):
        enabled, disabled_lines = self._integration_rows(IntegrationMonitor.get_instance().get_all_statuses())
        for status in enabled:
            screen.set_color(self.STATUS_STATE_COLORS.get(status["state"], Color(100, 100, 100)))
            screen.write_at(x, y, "●")
            screen.set_color_rgb(150, 150, 150)
            screen.write_at(x + 3, y, status['name'])
            y += 1
        if disabled_lines:
            y += 1
            screen.set_color(self.STATUS_STATE_COLORS[IntegrationState.DISABLED.value])
            for line in disabled_lines:
                screen.write_at(x, y, line)
                y += 1

    def _draw_timing(self, screen: TUIScreen, x: int, y: int):
        screen.set_color_rgb(150, 150, 150)
        screen.set_bg_color_rgb(0, 0, 0)
        fg = Theme.COLOR_BLUE_FG_BRIGHTER
        bg = Theme.COLOR_BLUE_BG_BRIGHTER
        bar_len = 15
        console = self.console

        # Each row is the delta from the row above it; each bar shows that stage's
        # share of the total, so the stages visually add up to the Total bar below.
        # Suffixes annotate the source behind a stage's time: which gate started the
        # winning decode (RMS energy vs silero VAD), and whether the LLM reply / voice
        # audio were served from cache instead of generated.
        stages = [
            ("Silence detect", console.time_to_silence_detect,
             f" ({console.stt_trigger.upper()})" if console.stt_trigger else ""),
            ("Speech to text", console.time_to_first_word, ""),
            ("AI first token", console.time_to_first_token,
             " (cached)" if console.llm_from_cache else ""),
            ("AI thought for", console.time_to_user_reply, ""),
            ("Voice synthesis", console.time_to_first_sound_chunk,
             " (cached)" if console.voice_from_cache else ""),
            ("Speaker latency", console.time_to_speaker, ""),
        ]
        total = sum(value for _, value, _ in stages if value > -1)

        for row, (name, value, suffix) in enumerate(stages):
            if value > -1:
                bar_value = value / total if total > 0 else 0
                # ljust so a suffix that disappears next request overwrites its residue.
                self._draw_labelled_bar(f"{name:>15}", f"{value}s{suffix}".ljust(16), bar_value, x, y + row, bar_len, bg, fg, screen)

        # Total bar is always full.
        if console.time_to_first_token > -1 and console.time_to_first_sound_chunk > -1:
            self._draw_labelled_bar(f"{'Total':>15}", f"{round(total, 2)}s", 1.0, x, y + len(stages), bar_len, bg, fg, screen)

    def _draw_resource_bars(self, screen: TUIScreen, y: int):
        screen.reset_color()
        console = self.console
        fg = Theme.COLOR_BLUE_FG_BRIGHTER
        bg = Theme.COLOR_BLUE_BG_BRIGHTER
        fg_warning = Theme.COLOR_RED_FG
        bg_warning = Theme.COLOR_RED_BG

        # Right-anchored column: sits left of the prompt-section bars, right of the timing bars.
        x = screen.width - 97
        x_stats = x + 16

        def ratio(part: float, whole: float) -> float:
            return part / whole if whole > 0 else 0

        photos_frac = ratio(console.photos_indexed, console.photos_total)
        voice_frac = ratio(console.voice_cache_size_gb, console.voice_cache_max_gb)
        db_max_gb = 1.0
        db_frac = ratio(console.db_size_gb, db_max_gb)
        total_mem_frac = ratio(console.system_used_memory_gb, console.system_total_memory_gb)
        proc_mem_frac = ratio(console.process_memory_gb, console.system_total_memory_gb)
        proc_cpu_frac = console.process_cpu_percent / 100.0
        sys_cpu_frac = console.system_cpu_percent / 100.0
        fd_frac = console.fd_count / 1024.0
        stt = STTConfidenceMonitor.get_instance()
        mic_accuracy = stt.rolling_avg_12h() if stt.mics else 0.0

        # (label, value text, fraction, warn) — warn flips the bar to the red palette.
        rows = [
            ("Processed photos", f"{console.photos_indexed}/{console.photos_total}", photos_frac, photos_frac < 0.9),
            ("Voice cache", f"{console.voice_cache_size_gb:.1f}/{console.voice_cache_max_gb:.1f} GB", voice_frac, voice_frac >= 0.8),
            ("Database", f"{console.db_size_gb:.1f}/1 GB", db_frac, db_frac >= 0.8),
            ("Total memory", f"{console.system_used_memory_gb:.1f}/{console.system_total_memory_gb:.1f} GB", total_mem_frac, total_mem_frac >= 0.8),
            ("Keep memory", f"{console.process_memory_gb:.1f} GB", proc_mem_frac, proc_mem_frac >= 0.8),
            ("Keep CPU", f"{console.process_cpu_percent:.0f}%", proc_cpu_frac, proc_cpu_frac >= 0.8),
            ("Total CPU", f"{console.system_cpu_percent:.0f}%", sys_cpu_frac, sys_cpu_frac >= 0.8),
            ("Open FDs", f"{console.fd_count}/1024", fd_frac, console.fd_count >= 819),
            ("Mic accuracy", f"{mic_accuracy * 100:.0f}%", mic_accuracy, bool(stt.mics) and mic_accuracy < 0.3),
        ]
        for row, (label, value_str, frac, warn) in enumerate(rows):
            self._draw_labelled_bar(label, value_str, frac, x_stats, y + row, 15,
                                    bg_warning if warn else bg, fg_warning if warn else fg, screen, True)

        self._draw_labelled_bar("Volume", f"{console.target_server_volume}%", console.target_server_volume / 100.0,
                                x + 10, y + 9, 15, bg, fg, screen)
        self._draw_labelled_bar("Background noise", f"{console.current_noise_db:.0f} dB", console.current_noise_db / 80.0,
                                x, y + 10, 15, bg, fg, screen)

        prefill_pct = OllamaLLM.prefill_progress()
        kv_ready = prefill_pct is None or prefill_pct == 100.0
        if prefill_pct is None:
            kv_value_str, kv_scaled = "Hot", 1.0
        else:
            kv_value_str, kv_scaled = f"{prefill_pct:.0f}%", prefill_pct / 100.0
        self._draw_labelled_bar("KV-Cache", kv_value_str, kv_scaled, x + 8, y + 11, 15,
                                bg if kv_ready else bg_warning, fg if kv_ready else fg_warning, screen)

        y += self.RESOURCE_ROWS
        for addr, (latency_ms, _) in list(console.outpost_latencies.items()):
            ip_only = addr.split(',')[0] if ',' in addr else addr
            ip_only = ip_only.replace("'", "").replace("(", "").replace(")", "")
            self._draw_labelled_bar(f"{'Outpost ' + ip_only:>25}", f"{latency_ms:.1f} ms", latency_ms / 200.0,
                                    x - 9, y, 15, bg, fg, screen)
            y += 1

    def _draw_prompt_sections(self, screen: TUIScreen, y: int):
        pm = self.console.prompt_manager
        if not pm:
            return
        stats = pm.get_section_stats()
        if not stats:
            return

        max_size = max(size for _, size, _ in stats) or 1
        fg = Theme.COLOR_BLUE_FG_BRIGHTER
        bg = Theme.COLOR_BLUE_BG_BRIGHTER
        dim_fg = Color(int(fg.r * 0.7), int(fg.g * 0.7), int(fg.b * 0.7))
        dim_bg = Color(int(bg.r * 0.7), int(bg.g * 0.7), int(bg.b * 0.7))

        # Right-anchored column at the screen edge.
        x = screen.width - 49
        now = time.time()

        for section_name, size_bytes, last_update_time in stats:
            time_ago = now - last_update_time if last_update_time > 0 else 0
            time_str = f"{int(time_ago)}s" if time_ago < 60 else f"{int(time_ago / 60)}m"

            label = f"{section_name.replace('_', ' ').capitalize():>15}"
            size_kb = size_bytes / 1024.0
            value_str = f"{size_kb:>5.1f} Kb" if int(time_ago) == 0 else f"{size_kb:>5.1f} Kb ({time_str})"

            # Sections that just changed light up for 30s so a refresh is visible.
            recent = 0 < int(time_ago) < 30
            self._draw_labelled_bar(label, value_str, size_bytes / max_size, x, y, 15,
                                    bg if recent else dim_bg, fg if recent else dim_fg, screen)
            y += 1

        if pm.prompt:
            history_bytes = next((size for name, size, _ in stats if name == 'history'), 0)
            total_size_kb = (len(pm.prompt.encode('utf-8')) + history_bytes) / 1024.0
            max_kb = 100.0
            over = total_size_kb > max_kb
            self._draw_labelled_bar(f"{'Total':>15}", f"{total_size_kb:>5.1f} Kb", min(total_size_kb / max_kb, 1.0), x, y, 15,
                                    Theme.COLOR_RED_BG if over else dim_bg,
                                    Theme.COLOR_RED_FG if over else dim_fg, screen)

    @staticmethod
    def _draw_labelled_bar(text: str, value_string: str, value_scaled: float, x: int, y: int, bar_length: int,
                           bg: Color, fg: Color, screen: TUIScreen, right_align_label: bool = False):
        if right_align_label:
            x -= len(text)
        screen.set_color(fg)
        screen.write_at(x, y, text)
        screen.draw_progress_bar(x + 1 + len(text), y, bar_length, value_scaled, fg, bg)
        screen.set_color(fg)
        screen.write_at(x + 1 + bar_length + len(text) + 1, y, value_string)

    # --- Anthropic Cost Chart ---

    MODEL_COLORS_LIST = [COLOR_CYAN, COLOR_PURPLE, COLOR_YELLOW, COLOR_GREEN, COLOR_PINK, Color(255, 140, 60)]

    def _draw_cost_chart(self, screen: TUIScreen, x: int, y: int, width: int, box_h: int):
        if box_h < 6 or width < 30:
            return

        models = self.cost_models
        daily_costs = self.daily_costs
        dates = self.cost_dates
        date_strs = self.cost_date_strs
        if not date_strs:
            return

        cost_summary = f"Month: ${self.total_cost_30d:.0f}  Week: ${self.total_cost_7d:.0f}  Day: ${self.total_cost_yesterday:.0f}"

        # Y-axis width
        y_axis_w = 6
        # Legend height: 1 line for legend + 1 for x-axis labels
        legend_h = 2
        # Box dimensions
        box_w = width
        chart_h = max(4, box_h - 2 - legend_h)  # subtract box borders + legend
        box_h = chart_h + 2 + legend_h  # chart rows + top/bottom border + legend

        self._draw_chart_box(screen, x, y, box_w, box_h, "Anthropic API Cost",
                             cost_summary if self.cost_last_updated > 0 else "", self.COLOR_CYAN)

        # Show loading/error state
        if self.cost_error and not daily_costs:
            screen.set_color(self.COLOR_DIM)
            screen.write_at(x + 3, y + 2, self.cost_error)
            screen.reset_color()
            return
        if not self.cost_last_updated:
            screen.set_color(self.COLOR_DIM)
            screen.write_at(x + 3, y + 2, "Loading cost data...")
            screen.reset_color()
            return

        chart_x = x + 2 + y_axis_w
        chart_y = y + 1
        chart_w = box_w - 4 - y_axis_w

        gap = 1
        bar_w = 2 if chart_w >= 90 else 1
        num_days = min((chart_w + gap) // (bar_w + gap), len(date_strs))
        if num_days <= 0:
            return

        display_date_strs = date_strs[-num_days:]
        display_dates = dates[-num_days:]

        max_cost = 0.0
        for ds in display_date_strs:
            day_data = daily_costs.get(ds, {})
            max_cost = max(max_cost, sum(day_data.get(m, 0) for m in models))
        if max_cost <= 0:
            max_cost = 1.0
        # Round up to a nice number
        nice_max = self._nice_ceil(max_cost)

        screen.fill_rect(chart_x, chart_y, chart_w, chart_h, bg_color=self.COLOR_PANEL_BG)
        self._fill_y_axis(screen, x + 2, chart_y, chart_h, y_axis_w)

        # Draw Y-axis labels (4 labels)
        num_labels = 4
        for i in range(num_labels + 1):
            label_val = nice_max * i / num_labels
            label_y = chart_y + chart_h - 1 - int((i / num_labels) * (chart_h - 1))
            label_str = f"{label_val:>4.0f}" if label_val >= 1 else f"{label_val:>4.1f}"
            if len(label_str) > y_axis_w:
                label_str = label_str[:y_axis_w]
            screen.set_color(self.COLOR_DIM)
            screen.set_bg_color(self.COLOR_PANEL_BG)
            screen.write_at(x + 2, label_y, label_str)

        # Draw bars
        for day_idx in range(num_days):
            col_x = chart_x + day_idx * (bar_w + gap)
            if col_x + bar_w > x + box_w - 1:
                break

            day_data = daily_costs.get(display_date_strs[day_idx], {})

            # Build segments for this day (bottom to top)
            segments = []
            for mi, m in enumerate(models):
                cost = day_data.get(m, 0)
                if cost > 0:
                    segments.append((mi, cost))

            if not segments:
                continue

            # Compute fractional row heights per segment
            row_positions = []
            cursor = 0.0
            for mi, cost in segments:
                rows = (cost / nice_max) * chart_h
                row_positions.append((mi, cursor, cursor + rows))
                cursor += rows

            cost_max_row = max(1, chart_h - 1)
            for row in range(chart_h):
                cell_y = chart_y + chart_h - 1 - row
                row_bottom = float(row)
                row_top = float(row + 1)
                grad_t = row / cost_max_row

                overlapping = []
                for mi, seg_start, seg_end in row_positions:
                    if seg_end <= row_bottom or seg_start >= row_top:
                        continue
                    overlapping.append((mi, max(seg_start, row_bottom), min(seg_end, row_top)))

                if not overlapping:
                    char = ' '
                    fg_color = self.COLOR_PANEL_BG
                    bg_color = self.COLOR_PANEL_BG
                elif len(overlapping) == 1:
                    mi, cs, ce = overlapping[0]
                    base = self.MODEL_COLORS_LIST[mi % len(self.MODEL_COLORS_LIST)]
                    color = self._lerp_color(
                        self._lerp_color(self.COLOR_PANEL_BG, base, 0.35), base, grad_t
                    )
                    fill = ce - cs
                    if fill >= 0.99:
                        char = '\u2588'
                        fg_color = color
                        bg_color = color
                    else:
                        block_idx = max(1, min(7, round(fill * 8)))
                        char = self.BLOCK_CHARS[block_idx]
                        fg_color = color
                        bg_color = self.COLOR_PANEL_BG
                else:
                    overlapping.sort(key=lambda s: s[1])
                    mi_low = overlapping[0][0]
                    mi_high = overlapping[-1][0]
                    base_low = self.MODEL_COLORS_LIST[mi_low % len(self.MODEL_COLORS_LIST)]
                    base_high = self.MODEL_COLORS_LIST[mi_high % len(self.MODEL_COLORS_LIST)]
                    color_low = self._lerp_color(
                        self._lerp_color(self.COLOR_PANEL_BG, base_low, 0.35), base_low, grad_t
                    )
                    color_high = self._lerp_color(
                        self._lerp_color(self.COLOR_PANEL_BG, base_high, 0.35), base_high, grad_t
                    )
                    fill_low = overlapping[0][2] - row_bottom
                    fill_total = overlapping[-1][2] - row_bottom
                    if fill_total >= 0.99:
                        # Row fully covered: render as dual-color block (lower seg color in
                        # the bottom portion via fg, upper seg color in the rest via bg).
                        block_idx = max(1, min(7, round(fill_low * 8)))
                        char = self.BLOCK_CHARS[block_idx]
                        fg_color = color_low
                        bg_color = color_high
                    else:
                        # Segments don't reach row_top — render only the covered portion
                        # against the panel background. Without this, bg_color = color_high
                        # would paint the empty upper portion of the cell and visually
                        # inflate the bar to a full row.
                        block_idx = max(1, min(7, round(fill_total * 8)))
                        char = self.BLOCK_CHARS[block_idx]
                        fill_high = fill_total - fill_low
                        fg_color = color_low if fill_low >= fill_high else color_high
                        bg_color = self.COLOR_PANEL_BG

                screen.set_color(fg_color)
                screen.set_bg_color(bg_color)
                for bw in range(bar_w):
                    screen.write_at(col_x + bw, cell_y, char)

            screen.reset_color()

        label_y = chart_y + chart_h
        label_step = max(1, num_days // 5)
        label_indices = list(range(0, num_days, label_step))
        if label_indices[-1] != num_days - 1:
            label_indices.append(num_days - 1)
        for idx in label_indices:
            lx = chart_x + idx * (bar_w + gap)
            if lx + 5 > x + box_w - 1:
                break
            label = display_dates[idx].strftime("%b %-d")
            screen.set_color(self.COLOR_DIM)
            screen.set_bg_color(Color(0, 0, 0))
            screen.write_at(lx, label_y, label)
        screen.reset_color()

        # Legend row — centered under the chart area
        legend_y = chart_y + chart_h + 1
        entries = []
        for mi, m in enumerate(models):
            short_name = self._shorten_model_name(m)
            entries.append((mi, short_name, len(f"\u2588\u2588 {short_name}  ")))
        total_legend_w = sum(e[2] for e in entries) - 2 if entries else 0
        lx = chart_x + max(0, (chart_w - total_legend_w) // 2)
        for mi, short_name, entry_w in entries:
            if lx + entry_w > x + box_w - 2:
                break
            base = self.MODEL_COLORS_LIST[mi % len(self.MODEL_COLORS_LIST)]
            screen.set_color(self._lerp_color(self.COLOR_PANEL_BG, base, 0.35))
            screen.write_at(lx, legend_y, "\u2588\u2588")
            screen.set_color(self.COLOR_LABEL)
            screen.write_at(lx + 2, legend_y, f" {short_name}")
            lx += entry_w
        screen.reset_color()

    @staticmethod
    def _shorten_model_name(model: str) -> str:
        # "claude-sonnet-4-20250514" -> "sonnet-4"
        # "claude-3-5-haiku-20241022" -> "haiku-3.5"
        name = model.lower()
        name = name.replace("claude-", "")
        # Remove date suffix like "-20250514"
        parts = name.split("-")
        cleaned = []
        for p in parts:
            if len(p) == 8 and p.isdigit():
                continue
            cleaned.append(p)
        return "-".join(cleaned)

    @staticmethod
    def _lerp_color(c1: Color, c2: Color, t: float) -> Color:
        t = max(0.0, min(1.0, t))
        return Color(
            int(c1.r + (c2.r - c1.r) * t),
            int(c1.g + (c2.g - c1.g) * t),
            int(c1.b + (c2.b - c1.b) * t)
        )

    @staticmethod
    def _nice_ceil(value: float) -> float:
        if value <= 0:
            return 1.0
        magnitude = 10 ** math.floor(math.log10(value))
        normalized = value / magnitude
        if normalized <= 1:
            nice = 1
        elif normalized <= 2:
            nice = 2
        elif normalized <= 5:
            nice = 5
        else:
            nice = 10
        return nice * magnitude
