"""The knobs of the Crystal scene, tweakable while it runs: one `Param` per constant that
shapes the picture, with a sensible step, and a `Tuning` holding their current
values plus the cursor the tab's arrow keys move. The defaults are the module constants of
`Renderer` and `Shapes`, so a Tuning fresh out of the box renders exactly what the code
says; `lights()`, `sky()` and `materials()` turn the values back into renderer inputs. The
`fly_*`, `hold_*`, `glide_*`, `settle_*`, `logo_*` and `final_*` params shape the intro
flight (`Intro`): where it starts and arrives, how long each stage takes, where and how big
the logo ends up and the pose, in degrees, it settles in. The look — the pose and everything
from `ambient` down — has a second set of values, `LOGO_LOOK`, the logo is drawn with: over
the last `fade_seconds` of the flight the picture fades from the tuning's values to those,
`faded(u)` being the values `u` of the way there."""
from typing import Dict, List, Mapping, NamedTuple, Tuple

from interfaces.console.crystal.Renderer import (AMBIENT, AO_RADIUS, AO_STRENGTH, LIGHTS, SKY_STOPS,
                                                 Light, Material, SkyStops)
from interfaces.console.crystal.Shapes import CHAMFER, GREEN, SILVER
from common.logging import info


class Param(NamedTuple):
    key: str
    label: str
    default: float
    step: float
    decimals: int = 2

    def fmt(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"


KEY, FILL = LIGHTS
GROUND, ZENITH = SKY_STOPS[0][1], SKY_STOPS[-1][1]
HORIZON = SKY_STOPS[2][0]
# Where the two horizon stops sit between ground and zenith.
HORIZON_FRACTIONS = (0.25, 0.75)

PARAMS: Tuple[Param, ...] = (
    Param("gap", "Gap", 0.10, 0.02),
    Param("chamfer", "Chamfer", CHAMFER, 0.01, 3),
    Param("distance", "Camera distance", 5.0, 0.25),
    Param("zoom", "Zoom", 1.0, 0.05),
    Param("spin", "Spin speed", 1.0, 0.1, 1),
    Param("fly_from", "Fly-in from", 500, 10, 0),
    Param("fly_to", "Fly-in to", 3.7, 0.1, 1),
    Param("fly_seconds", "Fly-in seconds", 3.5, 0.1, 1),
    Param("fly_curve", "Fly-in curve", 0.0, 0.1, 1),
    Param("hold_seconds", "Hold seconds", 3.0, 0.1, 1),
    Param("glide_seconds", "Glide seconds", 3.0, 0.1, 1),
    Param("ease", "Ease", 2.0, 0.1, 1),
    Param("logo_distance", "Logo distance", 10.5, 0.5, 1),
    Param("logo_x", "Logo x", 0.10, 0.01),
    Param("logo_y", "Logo y", 0.17, 0.01),
    Param("logo_spin", "Logo spin", 0.3, 0.1, 1),
    Param("settle_seconds", "Settle seconds", 2.0, 0.1, 1),
    Param("fade_seconds", "Fade seconds", 4.0, 0.1, 1),
    Param("final_x", "Final tilt x", -46, 1, 0),
    Param("final_y", "Final turn y", 80, 1, 0),
    Param("final_z", "Final roll z", 180, 1, 0),
    Param("ambient", "Ambient", AMBIENT, 0.02),
    Param("key_x", "Key light x", KEY.position[0], 0.5, 1),
    Param("key_y", "Key light y", KEY.position[1], 0.5, 1),
    Param("key_z", "Key light z", KEY.position[2], 0.5, 1),
    Param("key_power", "Key light power", 1.0, 0.1, 1),
    Param("fill_power", "Fill light power", 1.0, 0.1, 1),
    Param("ao_radius", "Occlusion reach", AO_RADIUS, 0.05),
    Param("ao_strength", "Occlusion depth", AO_STRENGTH, 0.05),
    Param("sky", "Sky brightness", ZENITH[0], 0.05),
    Param("ground", "Ground brightness", GROUND[0], 0.02),
    Param("horizon", "Horizon width", HORIZON, 0.02),
    Param("green_gloss", "Green gloss", GREEN.gloss, 0.05),
    Param("green_shine", "Green shininess", GREEN.shininess, 5, 0),
    Param("green_mirror", "Green mirror", GREEN.reflectivity, 0.02),
    Param("silver_gloss", "Silver gloss", SILVER.gloss, 0.05),
    Param("silver_shine", "Silver shininess", SILVER.shininess, 5, 0),
    Param("silver_mirror", "Silver mirror", SILVER.reflectivity, 0.02),
)
BY_KEY: Dict[str, Param] = {p.key: p for p in PARAMS}

# The look the logo settles into: the same keys as the tuning's look params, faded to over
# the flight's last `fade_seconds`. A key not listed here keeps the tuning's value throughout.
LOGO_LOOK: Dict[str, float] = {
    "final_x": -45, "final_y": 90, "final_z": 0,
    "ambient": 0.76,
    "key_x": 61.5, "key_y": 67.5, "key_z": -4.5, "key_power": -3.8, "fill_power": 1.0,
    "ao_radius": 2.20, "ao_strength": 0.90,
    "sky": -0.25, "ground": -0.16, "horizon": -0.30,
    "green_gloss": 0.40, "green_shine": 90, "green_mirror": -0.10,
    "silver_gloss": 0.00, "silver_shine": 65, "silver_mirror": 0.12,
}
assert set(LOGO_LOOK) <= set(BY_KEY)


def _scaled(color: Tuple[float, float, float], factor: float) -> Tuple[float, float, float]:
    return tuple(c * factor for c in color)


class Tuning:
    """Current values of every `Param` and the cursor over them, plus `logo`, the look the
    intro fades to; the `fade` argument of the renderer-input methods is how far along
    that fade they read the values, 0 the tuning's own. The editing methods take `logo`:
    True while the logo's look is the one on screen, so a look key's edit lands in `logo` —
    the tuning's own value has no weight in the picture then."""

    def __init__(self):
        self.values: Dict[str, float] = {p.key: p.default for p in PARAMS}
        self.logo: Dict[str, float] = dict(LOGO_LOOK)
        self.selected = 0

    def __getitem__(self, key: str) -> float:
        return self.values[key]

    def faded(self, fade: float) -> Dict[str, float]:
        """Every value, the look keys `fade` (0..1) of the way from the tuning's to the logo's."""
        if fade <= 0.0:
            return dict(self.values)
        return {key: value + (self.logo[key] - value) * fade if key in self.logo else value
                for key, value in self.values.items()}

    def value(self, key: str, fade: float = 0.0) -> float:
        value = self.values[key]
        return value + (self.logo[key] - value) * fade if key in self.logo and fade > 0.0 else value

    @staticmethod
    def param(key: str) -> Param:
        return BY_KEY[key]

    @property
    def current(self) -> Param:
        return PARAMS[self.selected]

    def edits_logo(self, key: str, logo: bool) -> bool:
        """True when an edit of `key` goes to the logo's look: `logo` says that look is the
        one on screen, and only the look keys have a second value."""
        return logo and key in self.logo

    def shown(self, key: str, logo: bool = False) -> float:
        """The value an edit would change: the logo's for a look key while `logo`."""
        return self.logo[key] if self.edits_logo(key, logo) else self.values[key]

    def changed(self, key: str, logo: bool = False) -> bool:
        if self.edits_logo(key, logo):
            return self.logo[key] != LOGO_LOOK[key]
        return self.values[key] != BY_KEY[key].default

    def select(self, delta: int) -> Param:
        self.selected = (self.selected + delta) % len(PARAMS)
        return self.current

    def set(self, key: str, value: float, logo: bool = False) -> float:
        """Round to the param's precision; returns what was stored. There is no range: a
        knob goes wherever it is turned."""
        target = self.logo if self.edits_logo(key, logo) else self.values
        target[key] = round(value, BY_KEY[key].decimals)
        return target[key]

    def adjust(self, key: str, steps: int, logo: bool = False) -> float:
        p = BY_KEY[key]
        value = self.set(key, self.shown(key, logo) + steps * p.step, logo)
        which = "logo " if self.edits_logo(key, logo) else ""
        info(f"Crystal: {which}{p.label} = {p.fmt(value)}{'' if self.changed(key, logo) else ' (default)'}.")
        return value

    def nudge(self, steps: int, logo: bool = False) -> Param:
        """Adjust the selected param by `steps` steps."""
        self.adjust(self.current.key, steps, logo)
        return self.current

    def reset(self) -> List[str]:
        """Both looks back to the defaults; returns the labels of the params that were changed."""
        changed = [p.label for p in PARAMS if self.changed(p.key) or self.changed(p.key, logo=True)]
        self.values = {p.key: p.default for p in PARAMS}
        self.logo = dict(LOGO_LOOK)
        info(f"Crystal: reset {', '.join(changed)}." if changed else "Crystal: nothing to reset.")
        return changed

    def lights(self, fade: float = 0.0) -> Tuple[Light, Light]:
        return lights(self.faded(fade))

    def sky(self, fade: float = 0.0) -> SkyStops:
        return sky(self.faded(fade))

    def materials(self, fade: float = 0.0) -> Tuple[Material, Material]:
        return materials(self.faded(fade))


def lights(v: Mapping[str, float]) -> Tuple[Light, Light]:
    return (Light((v["key_x"], v["key_y"], v["key_z"]), _scaled(KEY.color, v["key_power"])),
            Light(FILL.position, _scaled(FILL.color, v["fill_power"])))


def sky(v: Mapping[str, float]) -> SkyStops:
    """The ground and zenith scaled to their brightnesses, the two horizon stops sitting
    between them at the same fractions as by default, the band widened or narrowed."""
    ground = _scaled(GROUND, v["ground"] / GROUND[0])
    zenith = _scaled(ZENITH, v["sky"] / ZENITH[0])
    w = v["horizon"]
    low, high = (tuple(lo + f * (hi - lo) for lo, hi in zip(ground, zenith))
                 for f in HORIZON_FRACTIONS)
    return ((-1.0, ground), (-w, low), (w, high), (1.0, zenith))


def materials(v: Mapping[str, float]) -> Tuple[Material, Material]:
    return (GREEN._replace(gloss=v["green_gloss"], shininess=v["green_shine"], reflectivity=v["green_mirror"]),
            SILVER._replace(gloss=v["silver_gloss"], shininess=v["silver_shine"], reflectivity=v["silver_mirror"]))
