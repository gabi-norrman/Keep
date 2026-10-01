"""The console's one palette and its margins: every tab and widget takes its colours from
here, naming its own roles (a header, a selected row, a diff gutter) in terms of these, so
the whole TUI reads as one piece. A new colour belongs here, not in the tab that needs it.

Three groups: the surfaces things sit on, the text levels from bright to faint, and the
accents — each with a deep companion a bar or column shades up from and a dim tint for the
ground behind it."""
from interfaces.console.framework.TUIScreen import Color

LEFT_MARGIN = 5
TOP_MARGIN = 5   # the Home chat starts one row under CONTENT_TOP, for air below the tab bar


def mix(a: Color, b: Color, t: float) -> Color:
    """The colour `t` of the way (0..1) from `a` to `b`."""
    t = max(0.0, min(1.0, t))
    return Color(int(a.r + (b.r - a.r) * t), int(a.g + (b.g - a.g) * t), int(a.b + (b.b - a.b) * t))


# Surfaces, darkest first; the screen's own ground is TUIScreen.BACKGROUND, black.
COLOR_PANEL_BG = Color(14, 16, 24)       # a tile's body, a table's striped row
COLOR_TITLE_BG = Color(24, 27, 40)       # a tile's title strip, a table header, an unselected tab
COLOR_SELECTED_BG = Color(36, 42, 68)    # the row or item under the cursor
COLOR_GRID = Color(38, 42, 60)           # chart rules, bar-row fill, a scrollbar's track

# Text, brightest first.
COLOR_BRIGHT = Color(225, 230, 240)      # what must stand out: a selected tab, bold
COLOR_TEXT = Color(204, 209, 222)        # body text
COLOR_TITLE = Color(176, 183, 204)       # titles and column headers
COLOR_LABEL = Color(140, 147, 166)       # labels, secondary text
COLOR_DIM = Color(92, 98, 118)           # hints, units, what is switched off
COLOR_FAINT = Color(68, 73, 92)          # rules and asides that should barely register

# Accents.
COLOR_CYAN = Color(64, 200, 255)
COLOR_TEAL = Color(70, 215, 200)
COLOR_GREEN = Color(70, 230, 150)
COLOR_YELLOW = Color(255, 205, 80)
COLOR_ORANGE = Color(255, 150, 70)
COLOR_RED = Color(255, 90, 90)
COLOR_PINK = Color(255, 105, 180)
COLOR_PURPLE = Color(175, 130, 255)
COLOR_BLUE = Color(110, 150, 255)

# The ground behind an accent: a meter's track, a plate under text of that colour.
COLOR_CYAN_DIM = Color(14, 42, 58)
COLOR_GREEN_DIM = Color(14, 50, 36)
COLOR_YELLOW_DIM = Color(56, 44, 16)
COLOR_RED_DIM = Color(58, 20, 22)
COLOR_PINK_DIM = Color(58, 24, 44)
COLOR_BLUE_DIM = Color(24, 34, 62)

# The colour a bar or meter starts from before it reaches its own: a neighbouring, deeper
# hue, so a fill reads as a gradient and not as a slab.
DEEP = {
    COLOR_CYAN: Color(40, 80, 225),
    COLOR_TEAL: Color(0, 110, 150),
    COLOR_GREEN: Color(0, 125, 140),
    COLOR_YELLOW: Color(225, 95, 40),
    COLOR_ORANGE: Color(190, 50, 70),
    COLOR_RED: Color(150, 20, 70),
    COLOR_PINK: Color(125, 60, 220),
    COLOR_PURPLE: Color(70, 70, 210),
    COLOR_BLUE: Color(70, 60, 180),
}


def deep(color: Color) -> Color:
    """`color`'s deep companion; a colour outside the accents fades up from the panel ground."""
    return DEEP.get(color) or mix(COLOR_PANEL_BG, color, 0.35)


COLOR_SECRET = COLOR_ORANGE   # a vaulted value wherever it shows: revealed in Vault, masked elsewhere
