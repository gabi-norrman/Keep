"""Philips Hue's CLIP v2 resources as the home automation data model, pure functions. The
lights and the rooms' grouped lights are the LIGHTS, the scenes the ACTIONS, the motion
sensors' readings the SENSORS (no VARIABLES); the name index turns the names the LLM hands back
into the resource a command PUTs to. A light's room comes through its device — a room's
children are devices, a light's owner is one — and a zone or the bridge's whole-house group
shows as a grouped light of its own."""
import difflib
import re
from dataclasses import dataclass
from typing import Callable, Optional

from assistant.integration.HomeAutomation import SECTIONS, Entity, HomeAutomation, HomeSnapshot

# What a snapshot reads from the bridge, one `GET /clip/v2/resource/<type>` each.
RESOURCE_TYPES = ("light", "grouped_light", "room", "zone", "scene", "device", "temperature", "motion", "light_level")
LIGHT_TYPES = ("light", "grouped_light")
GROUP_NAME = "All lights"           # a room's or zone's grouped light, the room as its area
WHOLE_HOUSE = "Whole house"         # the bridge's own group over every light
_SPACES = re.compile(r"\s+")
_PAREN_SUFFIX = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


@dataclass(frozen=True)
class Target:
    """What a name resolves to: the resource a command PUTs to, and the name as the prompt showed it."""
    rtype: str
    rid: str
    display: str


class Unresolved(Exception):
    """A name the index cannot turn into one resource; the message is for the LLM."""


def normalize(text: str) -> str:
    return _SPACES.sub(" ", HomeAutomation.from_display(text)).strip().lower()


class NameIndex:
    """Name → Target for the commands. Every entity is reachable as `name (area)` (what
    `from_display` hands the backend for the `"name [Area]"` the prompt showed) and as its
    bare name when that is unique."""

    def __init__(self):
        self._targets: dict[str, Optional[Target]] = {}      # normalized key → target, None when ambiguous
        self._holders: dict[str, list[Target]] = {}

    def add(self, target: Target, name: str, area: Optional[str]):
        self._put(name, target)
        if area:
            self._put(f"{name} ({area})", target)

    def _put(self, key: str, target: Target):
        key = normalize(key)
        holders = self._holders.setdefault(key, [])
        if target not in holders:
            holders.append(target)
        self._targets[key] = target if len(holders) == 1 else None

    def resolve(self, name: str) -> Target:
        """The target behind *name*, else Unresolved with the text for the LLM."""
        key = normalize(name)
        if not key:
            raise Unresolved("No name given.")
        for candidate in self._candidates(key):
            if candidate in self._targets:
                target = self._targets[candidate]
                if target is not None:
                    return target
                options = ", ".join(t.display for t in self._holders[candidate])
                raise Unresolved(f'"{name}" could be {options} — say which.')
        close = difflib.get_close_matches(key, [k for k, t in self._targets.items() if t is not None], n=3, cutoff=0.6)
        suggestions = sorted({self._targets[k].display for k in close})
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise Unresolved(f'Hue has nothing named "{name}".{hint}')

    @staticmethod
    def _candidates(key: str) -> list[str]:
        """The key itself, then — for `name (place)` — the bare name: the LLM may have
        invented the place or translated it."""
        match = _PAREN_SUFFIX.match(key)
        if match and match.group(1):
            return [key, match.group(1).strip()]
        return [key]


def _name(resource: dict) -> str:
    return str((resource.get("metadata") or {}).get("name") or resource.get("id_v1") or resource["id"]).strip()


def _owner(resource: dict) -> Optional[str]:
    return (resource.get("owner") or {}).get("rid")


def light_value(resource: dict) -> str:
    """A light's or grouped light's state: `off`, its brightness as `NN%`, or `on` for one
    that cannot dim (a smart plug)."""
    on = (resource.get("on") or {}).get("on")
    if on is None:
        return "unknown"
    if not on:
        return "off"
    brightness = (resource.get("dimming") or {}).get("brightness")
    return f"{round(brightness)}%" if brightness is not None else "on"


def lux(light_level: float) -> int:
    """Hue reports 10000·log10(lux) + 1; back to lux, rounded."""
    return round(10 ** ((light_level - 1) / 10000))


def _reading(resource: dict, field: str, key: str) -> Optional[object]:
    """A sensor's value: `<field>.<key>` of the resource, or the `<field>.<key>_report` block
    the newer firmware moves it to; None when the bridge marks it invalid."""
    block = resource.get(field) or {}
    if block.get(f"{key}_valid") is False:
        return None
    value = block.get(key)
    if value is None:
        value = (block.get(f"{key}_report") or {}).get(key)
    return value


def _temperature(resource: dict) -> Optional[str]:
    value = _reading(resource, "temperature", "temperature")
    return f"{float(value):.1f} °C" if value is not None else None


def _motion(resource: dict) -> Optional[str]:
    value = _reading(resource, "motion", "motion")
    return ("detected" if value else "clear") if value is not None else None


def _light_level(resource: dict) -> Optional[str]:
    value = _reading(resource, "light", "light_level")
    return f"{lux(float(value))} lx" if value is not None else None


# resource type → (the suffix after the sensor's device name, the reading as text)
SENSOR_READERS: tuple[tuple[str, str, Callable[[dict], Optional[str]]], ...] = (
    ("temperature", "temperature", _temperature),
    ("motion", "motion", _motion),
    ("light_level", "light level", _light_level),
)


def build(resources: dict[str, list[dict]]) -> tuple[HomeSnapshot, NameIndex]:
    """The snapshot (sorted by name within each list) and the index of everything in it. Two
    entities of one name in one room are told apart by a number on the later one."""
    def of(rtype: str) -> list[dict]:
        return resources.get(rtype) or []

    groups = {g["id"]: _name(g) for g in of("room") + of("zone")}
    room_of_device = {child["rid"]: _name(room) for room in of("room")
                      for child in room.get("children") or () if child.get("rtype") == "device"}
    device_names = {d["id"]: _name(d) for d in of("device")}
    snapshot, index = HomeSnapshot(), NameIndex()
    seen: dict[tuple[str, Optional[str]], int] = {}

    def add(section: str, rtype: str, resource: dict, name: str, area: Optional[str], value: str):
        count = seen.get((name.lower(), area), 0) + 1
        seen[(name.lower(), area)] = count
        if count > 1:
            name = f"{name} {count}"
        target = Target(rtype, resource["id"], HomeAutomation.to_display(f"{name} ({area})" if area else name))
        index.add(target, name, area)
        getattr(snapshot, section).append(Entity(name, value, area))

    for light in of("light"):
        add("lights", "light", light, _name(light), room_of_device.get(_owner(light)), light_value(light))
    for group in of("grouped_light"):
        owner = _owner(group)
        if owner in groups:
            add("lights", "grouped_light", group, GROUP_NAME, groups[owner], light_value(group))
        elif (group.get("owner") or {}).get("rtype") == "bridge_home":
            add("lights", "grouped_light", group, WHOLE_HOUSE, None, light_value(group))
    for scene in of("scene"):
        active = (scene.get("status") or {}).get("active", "inactive") != "inactive"
        area = groups.get((scene.get("group") or {}).get("rid"))
        add("actions", "scene", scene, _name(scene), area, "active scene" if active else "scene")
    for rtype, suffix, reader in SENSOR_READERS:
        for sensor in of(rtype):
            if not sensor.get("enabled", True):
                continue
            value = reader(sensor)
            if value is None:
                continue
            device = _owner(sensor)
            add("sensors", rtype, sensor, f"{device_names.get(device, 'Hue sensor')} {suffix}",
                room_of_device.get(device), value)
    for section in SECTIONS:
        getattr(snapshot, section).sort(key=lambda e: (e.name.lower(), e.area or ""))
    return snapshot, index
