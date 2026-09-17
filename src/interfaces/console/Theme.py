"""The console's shared palette and margins: the constants more than one tab draws with.
A tab's own colours stay in the tab; these are the ones the Home, Voice and Dashboard tabs
share so the plates and bars look like one console."""
from interfaces.console.framework.TUIScreen import Color

LEFT_MARGIN = 5
TOP_MARGIN = 5   # the Home chat starts one row under CONTENT_TOP, for air below the tab bar

COLOR_YELLOW_FG = Color(185, 155, 81)
COLOR_YELLOW_BG = Color(int(18 * 1.3), int(15 * 1.3), int(5 * 1.3))

COLOR_BLUE_BG = Color(10, 21, 47)
COLOR_BLUE_FG_BRIGHTER = Color(int(104 * 1.3), int(114 * 1.3), int(141 * 1.5))
COLOR_BLUE_BG_BRIGHTER = Color(10, 23, 63)

COLOR_GREEN_BG = Color(12, 28, 21)
COLOR_GREEN_FG_BRIGHTER = Color(73, 216, 162)

COLOR_RED_FG = Color(143, 51, 49)
COLOR_RED_BG = Color(79, 10, 9)

COLOR_SECRET = Color(255, 140, 30)   # a vaulted value wherever it shows: revealed in Vault, masked elsewhere
