"""The intro flight of the Crystal scene: the object starts so far away it is a single pixel,
flies in to fill the middle of the screen, then glides up into the top-left corner and
shrinks to logo size, where its tumble winds down until it stands still. `Intro.shot(t)` is the camera at second `t` of the
flight — a pure function of the time and the tuning, so a restart is just the clock going
back to zero and every knob takes effect on the next frame.

The flight is two eased ramps added together, not two stages one after the other: the
fly-in ramps the apparent size up from the speck to the arrival size, the glide ramps it
down to logo size while moving the centre to the corner and slowing the tumble. Each ramp
is a smooth S curve of its own, and the glide may start before the fly-in has finished
(a negative hold): where they overlap their velocities add, so the object never comes to
rest between the two motions but bends from the one into the other.

The tumble is not accumulated frame by frame but read off the clock too: `turn(t)` is the
exact integral of the tumble rate, so the pose at any moment is a function of the time and
the tuning alone. That is what lets the flight be aimed at its end: `angles(t)` counts the
tumble still to come back from the pose the `final_*` params ask for, so the object always
settles on exactly that pose, however the timings are tuned.

The last `fade_seconds` of the flight fade the look to the logo's (`Tuning.LOGO_LOOK`):
`fade(t)` is that ramp, 0 until it starts and 1 when the object stands still, and the
pose the tumble is aimed at follows it, so the object settles on the logo's pose."""
import math
from typing import Callable, NamedTuple, Optional, Tuple

FLY_IN, HOLD, GLIDE, SETTLE, LOGO = "fly-in", "hold", "glide", "settle", "logo"
FRAME_SECONDS = 1 / 30           # the tumble's unit: spin speed 1 turns as much per second as 30 frames used to
TUMBLE = (0.017, 0.029, 0.011)   # radians about x, y and z per frame of tumble at spin speed 1


class Shot(NamedTuple):
    """Where the object is at one instant: its distance from the camera, where its centre sits
    on the surface as fractions across and down (0.5, 0.5 is the middle), how fast it
    tumbles relative to the tuning's spin speed, and the stage the flight is in."""
    stage: str
    distance: float
    x: float
    y: float
    spin: float


def ease(u: float, power: float = 1.0) -> float:
    """An S curve from 0 at 0 to 1 at 1, clamped outside. `power` 0 is a straight line, 1
    the cubic smoothstep (velocity 0 at both ends), 2 the quintic smootherstep (velocity
    and acceleration 0 at both ends, so the ramp is gentler still); between them blend."""
    u = min(1.0, max(0.0, u))
    cubic = u * u * (3 - 2 * u)
    quintic = u * u * u * (u * (6 * u - 15) + 10)
    if power <= 1.0:
        return u + (cubic - u) * power
    return cubic + (quintic - cubic) * (power - 1.0)


def ease_integral(u: float, power: float = 1.0) -> float:
    """The area under `ease` from 0 to `u`; past 1 the curve is flat at 1, so it grows by u - 1."""
    if u >= 1.0:
        return 0.5 + (u - 1.0)
    u = max(0.0, u)
    linear = u * u / 2
    cubic = u ** 3 - u ** 4 / 2
    quintic = u ** 6 - 3 * u ** 5 + 2.5 * u ** 4
    if power <= 1.0:
        return linear + (cubic - linear) * power
    return cubic + (quintic - cubic) * (power - 1.0)


def ramp(t: float, start: float, seconds: float, power: float) -> float:
    """How far along an eased ramp that starts at `start` and takes `seconds` we are at `t`."""
    return ease((t - start) / seconds, power) if seconds > 0 else float(t >= start)


def ramp_integral(t: float, start: float, seconds: float, power: float) -> float:
    """The area under that ramp up to `t`, in seconds."""
    if seconds <= 0:
        return max(0.0, t - start)
    return seconds * ease_integral((t - start) / seconds, power)


def approach(far: float, near: float, u: float, curve: float) -> float:
    """The distance `u` of the way from `far` to `near`. `curve` says what moves evenly:
    at 1 the apparent size (the distance's reciprocal), which grows fast at first since a
    speck doubles in no time; at 0 the size's logarithm, so every doubling takes the same
    time and the growth looks even; at -1 the distance itself, which keeps the object a
    speck for most of the flight and bursts it in at the end. In between the exponent
    blends: the distance to the power `curve` moves evenly."""
    if abs(curve) < 1e-6:
        return far * (near / far) ** u
    return (far ** -curve + (near ** -curve - far ** -curve) * u) ** (-1 / curve)


class Intro:
    """The flight read off a tuning: `values` looks a param up by key, `logo` looks up the
    value the same key fades to by the end of the flight (the pose stays put without it)."""

    def __init__(self, values: Callable[[str], float], logo: Optional[Callable[[str], float]] = None):
        self.values = values
        self.logo = logo if logo is not None else values

    def glide_start(self) -> float:
        return self.values("fly_seconds") + self.values("hold_seconds")

    def glide_end(self) -> float:
        return self.glide_start() + self.values("glide_seconds")

    def length(self) -> float:
        """Seconds until the object stands still as the logo."""
        return max(self.values("fly_seconds"), self.glide_end() + self.values("settle_seconds"))

    def fade(self, t: float) -> float:
        """How far the look has faded to the logo's at `t`: 0 until the last `fade_seconds`
        of the flight, 1 from its end on."""
        seconds = self.values("fade_seconds")
        return ramp(t, self.length() - seconds, seconds, self.values("ease"))

    def shot(self, t: float, logo_at: Optional[Tuple[float, float]] = None) -> Shot:
        """`logo_at` overrides where the glide takes the centre (fractions across and down),
        `logo_x` / `logo_y` when None."""
        v = self.values
        near, logo, power = v("fly_to"), v("logo_distance"), v("ease")
        logo_x, logo_y = logo_at if logo_at is not None else (v("logo_x"), v("logo_y"))
        fly = ramp(t, 0.0, v("fly_seconds"), power)
        glide = ramp(t, self.glide_start(), v("glide_seconds"), power)
        settle = ramp(t, self.glide_end(), v("settle_seconds"), power)
        size = 1 / approach(v("fly_from"), near, fly, v("fly_curve")) + (1 / logo - 1 / near) * glide
        if settle >= 1.0:
            stage = LOGO
        elif glide >= 1.0:
            stage = SETTLE
        elif glide > 0.0:
            stage = GLIDE
        elif fly >= 1.0:
            stage = HOLD
        else:
            stage = FLY_IN
        return Shot(stage, 1 / size,
                    0.5 + (logo_x - 0.5) * glide, 0.5 + (logo_y - 0.5) * glide,
                    (1.0 + (v("logo_spin") - 1.0) * glide) * (1.0 - settle))

    def turn(self, t: float) -> float:
        """Seconds of full-rate tumble done by `t`: the integral of the shot's spin, which is
        1 + (logo_spin - 1) * glide - logo_spin * settle (the settle ramp only runs once the
        glide is complete, so the two terms never overlap)."""
        v = self.values
        power, logo_spin = v("ease"), v("logo_spin")
        return (t + (logo_spin - 1.0) * ramp_integral(t, self.glide_start(), v("glide_seconds"), power)
                - logo_spin * ramp_integral(t, self.glide_end(), v("settle_seconds"), power))

    def angles(self, t: float) -> Tuple[float, float, float]:
        """The pose at `t`, in radians about x, y and z: the final pose with the tumble still
        to come undone, so at the end of the flight it is exactly the final pose."""
        v = self.values
        frames = (self.turn(t) - self.turn(self.length())) * v("spin") / FRAME_SECONDS
        fade = self.fade(t)
        return tuple(math.radians(v(key) + (self.logo(key) - v(key)) * fade) + frames * rate
                     for key, rate in zip(("final_x", "final_y", "final_z"), TUMBLE))
