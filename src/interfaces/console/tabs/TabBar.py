from dataclasses import dataclass
from typing import List, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import display_width, ljust

TAB_WIDTH = 10
# What stands in for the tabs a row has no room for, before and after the ones it shows
MORE_BEFORE = "‹ "
MORE_AFTER = "›"

COLOR_INACTIVE_FG = Theme.COLOR_DIM
COLOR_ACTIVE_FG = Theme.COLOR_BRIGHT
COLOR_SELECTED_UNFOCUSED_FG = Theme.COLOR_TITLE


@dataclass(frozen=True)
class BarStyle:
    tab_bg: Color        # inactive tabs
    separator: Color     # gaps between tabs
    fill: Color          # the rest of the row
    active_bg: Color     # the selected tab


TOP_BAR = BarStyle(tab_bg=Theme.COLOR_TITLE_BG, separator=Theme.COLOR_GRID, fill=Theme.COLOR_GRID,
                   active_bg=TUIScreen.BACKGROUND)
# The sub-tab row sits between the top bar's gray and a selected tab's black; a selected
# TabGroup in the top bar takes the row's colors so tab and row join up
_SUB_FILL = Theme.mix(Theme.COLOR_PANEL_BG, Theme.COLOR_TITLE_BG, 0.5)
SUB_BAR = BarStyle(tab_bg=Theme.COLOR_PANEL_BG, separator=_SUB_FILL, fill=_SUB_FILL,
                   active_bg=TUIScreen.BACKGROUND)
TOP_BAR_GROUP_SELECTED = BarStyle(tab_bg=TOP_BAR.tab_bg, separator=TOP_BAR.separator, fill=TOP_BAR.fill,
                                  active_bg=SUB_BAR.fill)


def fill_row(screen: TUIScreen, row: int, color: Color):
    """Paint a whole row in one colour (the bar's ground, or the spacer row under it)."""
    screen.set_bg_color(color)
    screen.set_color(color)
    screen.write_at(1, row, " " * screen.width)


def _page_end(slots: List[int], first: int, end: int, indent: int) -> int:
    """The column after the tabs [first, end) drawn from `indent`, with the markers that page needs."""
    x = indent if first == 0 else max(1, indent) + len(MORE_BEFORE)
    return x + sum(slots[first:end]) + (len(MORE_AFTER) if end < len(slots) else 0)


def _visible(slots: List[int], active: int, indent: int, limit: int) -> Tuple[int, int]:
    """The tabs [first, end) a row shows when they do not all end before column `limit`: the
    page the active tab is on. Pages are cut greedily from the first tab, each leaving room
    for the markers it needs, so the row stands still while the selection walks within a page
    and turns only when it steps over the page's edge; the last page fills up from the left
    instead of ending half empty. A page holds at least one tab, however narrow the row."""
    count = len(slots)
    if _page_end(slots, 0, count, indent) <= limit:
        return 0, count
    first = 0
    while True:
        end = first + 1
        while end < count and _page_end(slots, first, end + 1, indent) <= limit:
            end += 1
        if active < end:
            break
        first = end
    while end == count and first > 0 and _page_end(slots, first - 1, count, indent) <= limit:
        first -= 1
    return first, end


def draw_tab_bar(screen: TUIScreen, row: int, names: List[str], active: int, focused: bool,
                 style: BarStyle, hint: str = "", indent: int = 0, hint_bright: bool = False, center: str = ""):
    """Draw one row of tabs. The active tab is bright when this row has keyboard focus and
    muted when another row (the sub-tab bar, or the top bar above it) is being navigated.
    The hint is right-aligned; bright makes it white and bold. `center` sits in the hint's
    gray midway between the last tab and the hint, and is dropped when the gap is too narrow.

    The hint gives way to the tabs: when they do not all end before it, they get the whole row
    — whichever tab is selected, so the hint does not come and go with the selection. Tabs
    wider than the row itself are shown a page at a time (`_visible`), the page with the active
    tab on it, with `‹` at the left while tabs are hidden before it and `›` after the last one
    while tabs are hidden behind it; `center` is dropped then. The page depends only on the
    names, the selection and the width: nothing is remembered between frames."""
    fill_row(screen, row, style.fill)

    leads = ["  " if i == 0 and indent == 0 else " " for i in range(len(names))]
    widths = [max(TAB_WIDTH + len(lead) - 1, len(lead) + display_width(name) + 1) for lead, name in zip(leads, names)]
    slots = [width + 1 for width in widths]     # a tab and the separator after it
    selected = min(max(active, 0), len(names) - 1)
    limit = screen.width - len(hint) + 1 if hint else screen.width + 1
    first, end = _visible(slots, selected, indent, limit)
    if hint and (first, end) != (0, len(names)):
        hint = ""
        first, end = _visible(slots, selected, indent, screen.width + 1)
    paged = (first, end) != (0, len(names))

    x = indent if first == 0 else max(1, indent) + len(MORE_BEFORE)
    for i in range(first, min(end, len(names))):
        text = ljust(leads[i] + names[i], widths[i])
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
        x += widths[i]

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

    if paged:
        screen.set_bg_color(style.fill)
        screen.set_color(COLOR_SELECTED_UNFOCUSED_FG)
        if first > 0:
            screen.write_at(max(1, indent), row, MORE_BEFORE)
        if end < len(names) and x <= screen.width:
            screen.write_at(x, row, MORE_AFTER)
        screen.reset_color()
        center = ""

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
