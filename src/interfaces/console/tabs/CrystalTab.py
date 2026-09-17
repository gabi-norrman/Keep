from typing import Optional

from .TabView import TabView, SUB_CONTENT_TOP
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.crystal.Scene import Scene
from interfaces.console.crystal.Tuning import PARAMS
from common.logging import info

COLOR_STATUS = Color(110, 110, 110)
COLOR_PANEL_BG = Color(14, 14, 18)
COLOR_PANEL_FG = Color(150, 150, 156)
COLOR_PANEL_TITLE = Color(200, 200, 206)
COLOR_SELECTED_BG = Color(46, 46, 58)
COLOR_SELECTED_FG = Color(255, 255, 255)
COLOR_CHANGED = Color(185, 155, 81)
COLOR_SATURATED = Color(230, 70, 70)


class CrystalTab(TabView):
    """The tuning playground around the Crystal `Scene`: a tumbling cube cut into two
    interlocking pieces, a green L in a silver frame, every edge chamfered, lit per pixel and
    filling the whole tab two pixels per cell. The framebuffer follows the terminal size, so
    resizing the window is the performance test: the status line shows the surface size and
    a smoothed render+blit time. With anti-aliasing on the scene is rendered at two or four
    times the size and box-filtered down. Every constant that shapes the picture is a
    `Tuning` param: up/down pick one in the panel, left/right step it (once the intro fades
    to the logo's look the look params shown and stepped are the logo's, the panel titled
    "logo look"; `o` brings the orbit's back), `r` resets them all,
    `t` hides and shows the panel; `c` cycles shades per RGB channel (the scene drops them to
    8 itself the moment the link falls `Scene.DROP_LAG` behind — the status line's link lag
    goes red past that); `+`/`-` still open and close the gap directly.

    Showing the tab replays the intro flight the startup plays (`Intro`): the object comes
    in from a single pixel, holds in the middle, then glides into the top-left corner as the
    logo and its tumble winds down onto the pose the final-angle params ask for, the look
    fading to the logo's own (`Tuning.LOGO_LOOK`) over the last "Fade seconds". `i` replays
    it, `o` drops back to the plain orbit at the tuning's camera distance. The scene is the
    one the startup intro and the Home logo draw, so tuning here tunes those."""

    AA_FACTORS = (1, 2, 4)
    COLOR_LEVELS = (8, 16, 32, 64)
    PANEL_MARGIN, PANEL_Y = 2, SUB_CONTENT_TOP + 1   # the panel hugs the right edge, this many cells in
    LABEL_WIDTH = max(len(p.label) for p in PARAMS)
    VALUE_WIDTH = 7

    def __init__(self, scene: Optional[Scene] = None):
        self.scene = scene if scene is not None else Scene()
        self.tuning = self.scene.tuning
        self.panel = False

    @property
    def editing_logo(self) -> bool:
        """True once the intro has begun fading to the logo's look: from there the look
        params shown and stepped are the logo's, the ones the picture is heading for."""
        return self.scene.fade() > 0.0

    def draw(self, screen: TUIScreen, frame: int):
        scene = self.scene
        cols = screen.width
        rows = screen.height - 1 - SUB_CONTENT_TOP   # the hint row at the bottom stays free
        if cols < 1 or rows < 1:
            return
        drawn = scene.draw(screen, 1, SUB_CONTENT_TOP, cols, rows)

        if self.panel:
            self._draw_panel(screen, rows)

        surface = scene.surface
        aa = f"AA {scene.aa}x{scene.aa}" if scene.aa > 1 else "AA off"
        state = "paused" if scene.paused else "running"
        flight = "orbit" if scene.shot is None else f"intro {scene.shot.stage} {scene.flight:.1f}s"
        link = screen.link_status
        screen.link_probe_wanted = True
        lag = "n/a" if link.unsupported else "--" if link.lag is None else f"{link.lag * 1000:.0f} ms"
        head = (f" Crystal  {surface.width}x{surface.height} px  {drawn} faces  "
                f"{scene.frame_ms:4.1f} ms/frame  {aa}  c {scene.color_levels} shades/ch  {state}  {flight}  "
                f"gap {self.tuning['gap']:.2f}  ")
        # The link lag is its own write so it can go red; nothing wraps onto the hint row.
        parts = [(COLOR_STATUS, head), (COLOR_SATURATED if link.saturated else COLOR_STATUS, f"link {lag}"),
                 (COLOR_STATUS, "   space pause  a antialias  arrows tune  t panel  r reset  i intro  o orbit ")]
        screen.reset_color()
        screen.move_cursor(1, screen.height - 1)
        room = screen.width
        for color, text in parts:
            screen.set_color(color)
            screen.write(text[:room])
            room -= len(text)
            if room <= 0:
                break

    def _draw_panel(self, screen: TUIScreen, rows: int):
        """The param list over the top-right of the picture, the selected row highlighted and
        values off their default in amber. When the list is taller than the tab it scrolls to
        keep the selection in view."""
        width = self.LABEL_WIDTH + self.VALUE_WIDTH + 4
        x = max(1, screen.width - self.PANEL_MARGIN - width + 1)
        visible = max(1, min(len(PARAMS), rows - 2))
        first = min(max(0, self.tuning.selected - visible // 2), len(PARAMS) - visible)
        screen.reset_color()
        screen.set_bg_color(COLOR_PANEL_BG)
        screen.set_color(COLOR_PANEL_TITLE)
        logo = self.editing_logo
        screen.write_at(x, self.PANEL_Y, f"{' Tuning: logo look' if logo else ' Tuning':<{width}}")
        for row, param in enumerate(PARAMS[first:first + visible]):
            selected = first + row == self.tuning.selected
            value = param.fmt(self.tuning.shown(param.key, logo))
            marker = "*" if self.tuning.changed(param.key, logo) else " "
            screen.reset_color()
            screen.set_bg_color(COLOR_SELECTED_BG if selected else COLOR_PANEL_BG)
            screen.set_color(COLOR_SELECTED_FG if selected else COLOR_PANEL_FG)
            screen.write_at(x, self.PANEL_Y + 1 + row,
                            f"{'>' if selected else ' '}{param.label:<{self.LABEL_WIDTH}} ")
            if marker == "*" and not selected:
                screen.set_color(COLOR_CHANGED)
            screen.write(f"{value:>{self.VALUE_WIDTH}}{marker} ")
        screen.reset_color()

    def handle_input(self, key: str) -> bool:
        scene = self.scene
        if key == ' ':
            scene.paused = not scene.paused
            info(f"Crystal: {'paused' if scene.paused else 'running'}.")
            return True
        if key in ('a', 'A'):
            scene.aa = self.AA_FACTORS[(self.AA_FACTORS.index(scene.aa) + 1) % len(self.AA_FACTORS)]
            info(f"Crystal: anti-aliasing {scene.aa}x{scene.aa}.")
            return True
        if key in ('c', 'C'):
            scene.color_levels = self.COLOR_LEVELS[(self.COLOR_LEVELS.index(scene.color_levels) + 1) % len(self.COLOR_LEVELS)]
            info(f"Crystal: {scene.color_levels} shades per RGB channel.")
            return True
        if key in ('t', 'T'):
            self.panel = not self.panel
            info(f"Crystal: tuning panel {'shown' if self.panel else 'hidden'}.")
            return True
        if key in ('r', 'R'):
            self.tuning.reset()
            scene.apply()
            return True
        if key in ('i', 'I'):
            scene.restart_intro()
            return True
        if key in ('o', 'O'):
            scene.stop_intro()
            return True
        if key in ('up', 'down'):
            self.panel = True
            self.tuning.select(-1 if key == 'up' else 1)
            return True
        if key in ('left', 'right'):
            self.panel = True
            self.tuning.nudge(-1 if key == 'left' else 1, self.editing_logo)
            scene.apply()
            return True
        if key in ('+', '=', '-'):
            self.tuning.adjust("gap", 1 if key != '-' else -1)
            scene.apply()
            return True
        return False

    def on_activate(self):
        info("Crystal tab shown.")
        self.scene.last_frame_at = None
        self.scene.restart_intro()
