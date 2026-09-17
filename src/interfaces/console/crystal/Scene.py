"""The Crystal scene as one object: the tuning, the renderer built from it, the surfaces that
follow whatever rectangle it is drawn into, the orbit's tumble and the intro flight's clock.
The startup intro (`StartupIntro`), the Home tab's logo and the Developer ▸ Crystal tab all
draw the same scene, so one tuning shapes all three."""
import time
from typing import Callable, Optional, Tuple

from interfaces.console.framework.LinkProbe import LinkStatus
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.crystal.Framebuffer import Framebuffer, LEVELS
from interfaces.console.crystal.Intro import Intro, Shot, FRAME_SECONDS, TUMBLE, LOGO
from interfaces.console.crystal.Renderer import Renderer, rotate
from interfaces.console.crystal.Shapes import GREEN, interlocking_cube
from interfaces.console.crystal.Tuning import Tuning, lights, materials, sky
from common.logging import info


class Scene:
    """`draw` advances the clock and draws the live frame; `draw_still` draws one instant of
    the flight without touching the clock, for the logo. The flight runs on real time
    (`clock`), so it lasts its seconds whatever the frame rate; pausing stops its clock,
    and the orbit's tumble along with it.

    The live picture also watches the link: `draw` asks for the link probes and, the first
    frame the terminal is `DROP_LAG` behind (an ssh session that cannot carry the frames
    shows as a stutter growing into pauses of seconds), drops the colour depth to
    `DROPPED_LEVELS` shades per channel — coarser steps mean fewer cells change per frame and
    longer runs of one colour, so a frame is a fraction of the bytes. The drop is once per
    lag episode and sticks (the Crystal tab's `c` key cycles it back by hand)."""

    FRAME_SECONDS = FRAME_SECONDS
    DROP_LAG = 0.2          # seconds behind past which the link is not keeping up with the picture
    DROPPED_LEVELS = 8
    MAX_FRAME_SECONDS = 0.25   # a frame later than this (the scene was hidden) advances the clock by this much
    # The logo's centre is `logo_x` / `logo_y` of the way across and down the surface, but
    # never so near an edge that the object is cut: its projected half-extent plus these
    # margins in pixels from the left and the top (four pixels are the tab bar's two rows,
    # so on the intro's full-screen surface the logo starts under it). A narrow or short
    # terminal is what moves it; where it fits, the fractions place it exactly.
    LOGO_LEFT_PX = 1
    LOGO_TOP_PX = 4

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.tuning = Tuning()
        self.renderer = Renderer(interlocking_cube(self.tuning["gap"], chamfer=self.tuning["chamfer"]))
        self.intro = Intro(self.tuning.__getitem__, lambda key: self.tuning.value(key, 1.0))
        self.clock = clock
        self.fb: Optional[Framebuffer] = None
        self.hires: Optional[Framebuffer] = None
        self.aa = 2
        self.color_levels = LEVELS
        self.lagging = False            # the link was DROP_LAG behind at the last frame
        self.paused = False
        self.tick = 0
        self.turn = 0.0                 # the orbit's accumulated tumble, in frames of FRAME_SECONDS
        self.frame_ms = 0.0
        self.drawn = 0                  # faces drawn by the last frame
        self.flight: Optional[float] = None   # seconds into the intro, None when it is not playing
        self.shot: Optional[Shot] = None
        self.last_frame_at: Optional[float] = None
        self.look_fade: Optional[float] = None   # how far to the logo's look the renderer was last set, None = stale

    @property
    def surface(self) -> Optional[Framebuffer]:
        return self.hires if self.aa > 1 else self.fb

    @property
    def landed(self) -> bool:
        """True once the flight has settled the object as the logo."""
        return self.shot is not None and self.shot.stage == LOGO

    def _surfaces(self, cols: int, rows: int):
        zoom = self.tuning["zoom"]
        if self.fb is None or (self.fb.width, self.fb.height, self.fb.zoom) != (cols, rows * 2, zoom):
            self.fb = Framebuffer(cols, rows * 2, zoom)
            self.hires = None
            info(f"Crystal: framebuffer {cols}x{rows * 2} px ({cols}x{rows} cells), zoom {zoom:.2f}.")
        if self.aa > 1 and (self.hires is None or self.hires.width != cols * self.aa):
            self.hires = Framebuffer(cols * self.aa, rows * 2 * self.aa, zoom)
            self.hires.look_at(*(c / n for c, n in zip(self.fb.centre, (self.fb.width, self.fb.height))))

    def draw(self, screen: TUIScreen, x: int, y: int, cols: int, rows: int) -> int:
        """Advance the clock and draw the frame `cols` cells wide and `rows` tall with its
        top-left cell at (x, y); returns the faces drawn."""
        if cols < 1 or rows < 1:
            return 0
        screen.link_probe_wanted = True
        self._govern(screen.link_status)
        self._surfaces(cols, rows)
        now = self.clock()
        dt = self.FRAME_SECONDS if self.last_frame_at is None else min(max(now - self.last_frame_at, 0.0), self.MAX_FRAME_SECONDS)
        self.last_frame_at = now
        if not self.paused:
            self.tick += 1
            if self.flight is not None:
                self.flight += dt
        self._aim()
        self._look(self.fade())
        if self.flight is not None:
            angles = self.intro.angles(self.flight)
        else:
            if not self.paused:
                self.turn += self.tuning["spin"] * dt / self.FRAME_SECONDS
            angles = tuple(self.turn * rate for rate in TUMBLE)
        started = time.perf_counter()
        self.drawn = self._render(angles)
        self.fb.blit(screen, x, y, levels=self.color_levels)
        ms = (time.perf_counter() - started) * 1000
        self.frame_ms = ms if self.frame_ms == 0 else self.frame_ms * 0.9 + ms * 0.1
        return self.drawn

    def _govern(self, link: LinkStatus):
        """Drop the colour depth the frame a lag episode starts; a later episode drops it
        again if it was cycled back up meanwhile."""
        lagging = link.lag is not None and link.lag >= self.DROP_LAG
        if lagging == self.lagging:
            return
        self.lagging = lagging
        if not lagging:
            behind = "latency unknown" if link.lag is None else f"{link.lag * 1000:.0f} ms behind"
            info(f"Crystal: link caught up ({behind}), staying at {self.color_levels} shades per channel.")
        elif self.color_levels > self.DROPPED_LEVELS:
            info(f"Crystal: link {link.lag * 1000:.0f} ms behind, dropping from {self.color_levels} "
                 f"to {self.DROPPED_LEVELS} shades per channel.")
            self.color_levels = self.DROPPED_LEVELS
        else:
            info(f"Crystal: link {link.lag * 1000:.0f} ms behind at {self.color_levels} shades per channel already.")

    def draw_still(self, screen: TUIScreen, x: int, y: int, cols: int, rows: int, t: float) -> int:
        """Draw second `t` of the flight into the same rectangle, the clock, the orbit and
        the live shot untouched: the logo is `t = intro.length()`."""
        if cols < 1 or rows < 1:
            return 0
        self._surfaces(cols, rows)
        shot = self.intro.shot(t, self.logo_at())
        self._place(shot.distance, shot.x, shot.y)
        self._look(self.intro.fade(t))
        drawn = self._render(self.intro.angles(t))
        self.fb.blit(screen, x, y, levels=self.color_levels)
        return drawn

    def _render(self, angles: Tuple[float, float, float]) -> int:
        if self.aa > 1:
            drawn = self.renderer.render(self.hires, *angles)
            self.hires.downsample_into(self.fb)
            return drawn
        return self.renderer.render(self.fb, *angles)

    def fade(self) -> float:
        """How far the live picture's look has faded to the logo's: 0 on the orbit."""
        return 0.0 if self.flight is None else self.intro.fade(self.flight)

    def _look(self, fade: float):
        """Give the renderer the look `fade` of the way from the tuning's to the logo's —
        the lights, the sky, the occlusion and each piece's material; nothing when it holds
        that already. The meshes stay: only their materials change."""
        if fade == self.look_fade:
            return
        if self.look_fade is not None and (self.look_fade == 0.0) != (fade == 0.0):
            if fade:
                info(f"Crystal: look fading to the logo's over {self.tuning['fade_seconds']:.1f}s.")
            else:
                info("Crystal: look back to the tuning's.")
        v = self.tuning.faded(fade)
        green, silver = materials(v)
        for mesh in self.renderer.meshes:
            mesh.material = green if mesh.material.color == GREEN.color else silver
        self.renderer.lights = lights(v)
        self.renderer.ambient = v["ambient"]
        self.renderer.ao_radius = v["ao_radius"]
        self.renderer.ao_strength = v["ao_strength"]
        self.renderer.sky = sky(v)
        self.look_fade = fade

    def _aim(self):
        """Place the object for this frame: the intro's shot while the flight plays, else the
        plain orbit at the tuning's camera distance."""
        if self.flight is None:
            self.shot = None
            self._place(self.tuning["distance"], 0.5, 0.5)
            return
        shot = self.intro.shot(self.flight, self.logo_at())
        if self.shot is None or shot.stage != self.shot.stage:
            info(f"Crystal: intro {shot.stage} at {self.flight:.2f}s, distance {shot.distance:.1f}.")
        self.shot = shot
        self._place(shot.distance, shot.x, shot.y)

    def logo_extent(self) -> Tuple[float, float]:
        """How far the logo reaches left/right and up/down from its centre on the current
        surface, in pixels: every vertex at the final pose, projected at the logo distance."""
        angles = self.intro.angles(self.intro.length())
        distance = self.tuning["logo_distance"]
        focal = self.fb.focal
        ex = ey = 0.0
        for mesh in self.renderer.meshes:
            for v in mesh.vertices:
                x, y, z = rotate(v, *angles)
                depth = distance - z
                ex = max(ex, abs(focal * x / depth))
                ey = max(ey, abs(focal * y / depth))
        return ex, ey

    def logo_at(self) -> Tuple[float, float]:
        """Where the logo's centre goes on the current surface, as fractions: the tuning's
        `logo_x` / `logo_y`, pushed right or down just enough to keep the whole object on
        the surface with the LOGO_*_PX margins."""
        fb = self.fb
        x, y = self.tuning["logo_x"], self.tuning["logo_y"]
        if fb is None:
            return x, y
        ex, ey = self.logo_extent()
        cx = max(x * fb.width, ex + self.LOGO_LEFT_PX)
        cy = max(y * fb.height, ey + self.LOGO_TOP_PX)
        return cx / fb.width, cy / fb.height

    def _place(self, distance: float, x: float, y: float):
        """The object stays on the view axis; the surfaces' principal point is what moves it
        about the screen, so it never skews and its centre lands exactly where asked."""
        self.renderer.distance = distance
        for surface in (self.fb, self.hires):
            if surface is not None:
                surface.look_at(x, y)

    def restart_intro(self):
        self.flight = 0.0
        self.shot = None
        info(f"Crystal: intro restarted, {self.intro.length():.1f}s to the logo.")

    def stop_intro(self) -> bool:
        """Back to the orbit; False when it was orbiting already."""
        if self.flight is None:
            info("Crystal: already orbiting.")
            return False
        self.flight = None
        info("Crystal: intro stopped, back to the orbit.")
        return True

    def skip_intro(self):
        """Jump the flight to its end: the next frame is the logo."""
        if self.flight is not None:
            info(f"Crystal: intro skipped at {self.flight:.1f}s.")
            self.flight = self.intro.length()

    def apply(self):
        """Push the tuning into the renderer, the look as far faded as the flight is; the
        surfaces follow the zoom on the next draw."""
        tuning = self.tuning
        self.renderer.meshes = interlocking_cube(tuning["gap"], chamfer=tuning["chamfer"])
        self.renderer.distance = tuning["distance"]
        self.look_fade = None
        self._look(self.fade())
