from dataclasses import dataclass
from typing import List

from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import display_width, ljust

TAB_WIDTH = 10

COLOR_INACTIVE_FG = Color(100, 105, 115)
COLOR_ACTIVE_FG = Color(220, 225, 235)
COLOR_SELECTED_UNFOCUSED_FG = Color(160, 165, 175)


@dataclass(frozen=True)
class BarStyle:
    tab_bg: Color        # inactive tabs
    separator: Color     # gaps between tabs
    fill: Color          # the rest of the row
    active_bg: Color     # the selected tab


TOP_BAR = BarStyle(tab_bg=Color(25, 28, 35), separator=Color(35, 38, 45), fill=Color(35, 38, 45),
                   active_bg=Color(0, 0, 0))
# The sub-tab row sits between the top bar's gray and a selected tab's black; a selected
# TabGroup in the top bar takes the row's colors so tab and row join up
SUB_BAR = BarStyle(tab_bg=Color(12, 14, 18), separator=Color(18, 20, 25), fill=Color(18, 20, 25),
                   active_bg=Color(0, 0, 0))
TOP_BAR_GROUP_SELECTED = BarStyle(tab_bg=TOP_BAR.tab_bg, separator=TOP_BAR.separator, fill=TOP_BAR.fill,
                                  active_bg=SUB_BAR.fill)


def fill_row(screen: TUIScreen, row: int, color: Color):
    """Paint a whole row in one colour (the bar's ground, or the spacer row under it)."""
    screen.set_bg_color(color)
    screen.set_color(color)
    screen.write_at(1, row, " " * screen.width)


def draw_tab_bar(screen: TUIScreen, row: int, names: List[str], active: int, focused: bool,
                 style: BarStyle, hint: str = "", indent: int = 0, hint_bright: bool = False, center: str = ""):
    """Draw one row of tabs. The active tab is bright when this row has keyboard focus and
    muted when another row (the sub-tab bar, or the top bar above it) is being navigated.
    The hint is right-aligned; bright makes it white and bold. `center` sits in the hint's
    gray midway between the last tab and the hint, and is dropped when the gap is too narrow."""
    fill_row(screen, row, style.fill)

    x = indent
    for i, name in enumerate(names):
        lead = "  " if i == 0 and indent == 0 else " "
        width = max(TAB_WIDTH + len(lead) - 1, len(lead) + display_width(name) + 1)
        text = ljust(lead + name, width)
        if i == active and focused:
            screen.set_bg_color(style.active_bg)
            screen.set_color(COLOR_ACTIVE_FG)
            screen.bold()
        elif i == active:
            screen.set_bg_color(style.active_bg)
            screen.set_color(COLOR_SELECTED_UNFOCUSED_FG)
        else:
            screen.set_bg_color(style.tab_bg)
            screen.set_color(COLOR_INACTIVE_FG)
        screen.write_at(x, row, text)
        x += width

        screen.set_bg_color(style.separator)
        screen.set_color(style.separator)
        screen.write_at(x, row, " ")
        screen.reset_color()
        x += 1

    screen.set_bg_color(style.fill)
    screen.set_color(style.fill)
    if x < screen.width:
        screen.write_at(x, row, " " * (screen.width - x))
    screen.write_at(0, row, " ")

    if hint:
        screen.set_color(COLOR_ACTIVE_FG if hint_bright else COLOR_INACTIVE_FG)
        if hint_bright:
            screen.bold()
        screen.write_at(screen.width - len(hint) + 1, row, hint)
        screen.reset_color()
    if center:
        gap = screen.width - len(hint) + 1 - x
        if gap >= len(center) + 2:
            screen.set_bg_color(style.fill)
            screen.set_color(COLOR_INACTIVE_FG)
            screen.write_at(x + (gap - len(center)) // 2, row, center)
    screen.reset_color()
