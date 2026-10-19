"""The HomeKit home as HomeClaw lists it, made into what the prompt shows and the commands
resolve: one Target per accessory (and one per zone of an irrigation control), keyed on the
UUID, never on the name — HomeKit lets three accessories in one room share a name.

The prompt groups the targets by room and ends every line with the target's short ref (the
first 8 hex digits of its UUID, longer where two would collide); a command takes that ref, the
full UUID, or a name that is unique — optionally with its room, "Name [Room]". A name that
matches several is refused with the candidates, never guessed. What another Keep integration
already brings is left out (`excluded`): the bridges themselves, and every accessory whose
bridge or maker matches `homeclaw.exclude_bridges`, unless its UUID is in `homeclaw.include`.
HomeKit's own action sets come through `list_scenes` named by a bare UUID; `real_scenes` drops
them."""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from assistant.integration.Feed import FeedEntry
from assistant.prompt.Held import HeldValues

UNREAD = "--"
VALVE_SERVICE = "000000D0-0000-1000-8000-0026BB765291"    # HomeKit's Valve service: one per zone
LIGHT, COVER, GARAGE, IRRIGATION, THERMOSTAT, SENSOR = (
    "lightbulb", "window_covering", "garage_door", "irrigation", "thermostat", "sensor")
SWITCHED = {LIGHT, "switch", "outlet"}
SKIPPED = {"bridge", "programmable_switch"}         # a bridge is no device; a button has no state to show
KIND_ORDER = (SENSOR, THERMOSTAT, COVER, GARAGE, IRRIGATION, LIGHT)
DEFAULT_BRIDGE_FILTERS = ("Plejd", "Philips hue", "Signify")
REF_LENGTH = 8
# A sensor reports to the decimal and every changed line costs a note in the history window, so the
# prompt shows readings rounded to a step and held until the fresh one is a full step away (Held).
# A battery is only worth a line when it is getting low.
TEMPERATURE_STEP_C = 0.5
HUMIDITY_STEP_PCT = 5
BATTERY_STEP_PCT = 10
BATTERY_SHOWN_BELOW_PCT = 20
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-f-]{6,36}$", re.I)
_ROOM_SUFFIX = re.compile(r"^(.*?)\s*[\[(]([^\])]+)[\])]\s*$")


class Unresolved(Exception):
    """A ref or name no target answers to, or several do; the text is for the LLM."""


def is_uuid(text) -> bool:
    return bool(_UUID.match(str(text or "").strip()))


def is_read(value) -> bool:
    return value not in (None, "", UNREAD)


def iso_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class Target:
    id: str                             # the accessory's UUID, what `control` gets
    name: str
    room: str
    kind: str                           # the HomeKit category; IRRIGATION for a zone too
    state: dict = field(default_factory=dict, compare=False)
    reachable: bool = True
    zone: Optional[str] = None          # a zone's name, for a Valve service of an irrigation control
    service_id: Optional[str] = None    # that Valve service's UUID: the selector `control` needs
    manufacturer: str = ""

    @property
    def key(self) -> str:
        """The UUID this target is known by: the zone's service for a zone, else the accessory."""
        return (self.service_id or self.id).upper()

    @property
    def label(self) -> str:
        name = f"{self.name}, zone {self.zone}" if self.zone else self.name
        return f"{name} [{self.room}]" if self.room else name


@dataclass
class Held:
    """The prompt's rounded-and-held readings, keyed by the target's ref; HomeClawIntegration owns one
    and hands it to `render`. Without one (the commands, the tests) a value is shown as read."""
    temperature: HeldValues = field(default_factory=lambda: HeldValues(TEMPERATURE_STEP_C))
    humidity: HeldValues = field(default_factory=lambda: HeldValues(HUMIDITY_STEP_PCT))
    battery: HeldValues = field(default_factory=lambda: HeldValues(BATTERY_STEP_PCT))


def excluded(accessory: dict, bridge_filters: Iterable[str], include: Iterable[str], exclude: Iterable[str]) -> bool:
    """Whether the accessory stays out: a UUID in `exclude`, a bridge, or a bridge or maker
    matching a filter (case-insensitive substring) — unless the UUID is in `include`."""
    uid = str(accessory.get("id") or "").upper()
    if uid in {str(u).upper() for u in include}:
        return False
    if uid in {str(u).upper() for u in exclude}:
        return True
    if accessory.get("is_bridge") or accessory.get("category") == "bridge":
        return True
    bridge = accessory.get("bridge") if isinstance(accessory.get("bridge"), dict) else {}
    haystack = " ".join(str(v or "") for v in (bridge.get("manufacturer"), bridge.get("name"),
                                                accessory.get("manufacturer"))).lower()
    return any(str(f).lower() in haystack for f in bridge_filters if f)


def real_scenes(scenes: list[dict]) -> list[dict]:
    """The scenes a person made: HomeKit's internal action sets carry a bare UUID as name."""
    return [s for s in scenes if s.get("name") and not is_uuid(s.get("name"))]


def build_targets(accessories: list[dict], zones: dict[str, list[dict]]) -> list[Target]:
    """The kept accessories as targets; an irrigation control with Valve services (`zones`,
    accessory UUID → [{id, name, active}]) is one target per zone instead."""
    targets = []
    for a in accessories:
        kind = str(a.get("category") or "other")
        if kind in SKIPPED:
            continue
        uid = str(a.get("id") or "").upper()
        common = dict(id=uid, name=str(a.get("name") or uid), room=str(a.get("room") or ""), kind=kind,
                      reachable=a.get("reachable") is not False, manufacturer=str(a.get("manufacturer") or ""))
        if kind == IRRIGATION and zones.get(uid):
            for zone in zones[uid]:
                targets.append(Target(state={"active": zone.get("active")}, zone=str(zone.get("name") or ""),
                                      service_id=str(zone.get("id") or "").upper() or None, **common))
        else:
            targets.append(Target(state=dict(a.get("state") or {}), **common))
    return targets


def zones_of(detail: dict) -> list[dict]:
    """The Valve services of a `get_accessory` answer, each {id, name, active}."""
    zones = []
    for service in detail.get("services") or []:
        if str(service.get("type") or "").upper() != VALVE_SERVICE:
            continue
        chars = {c.get("name"): c.get("value") for c in service.get("characteristics") or []}
        zones.append({"id": service.get("id"), "name": service.get("name"), "active": chars.get("active")})
    return zones


class Index:
    """Resolves what a command names to one target; built from every refresh."""

    def __init__(self, targets: list[Target]):
        self.targets = list(targets)
        self._refs = self._short_refs(self.targets)

    @staticmethod
    def _short_refs(targets: list[Target]) -> dict[str, str]:
        """key → the shortest prefix of at least REF_LENGTH that no other target shares."""
        refs = {}
        for t in targets:
            length = REF_LENGTH
            while length < len(t.key) and any(o.key != t.key and o.key.startswith(t.key[:length]) for o in targets):
                length += 1
            refs[t.key] = t.key[:length]
        return refs

    def ref(self, target: Target) -> str:
        return self._refs.get(target.key, target.key)

    def resolve(self, text: str, kinds: Iterable[str], what: str) -> Target:
        """The one target of `kinds` that `text` names; an empty `text` is the only one there
        is. Raises Unresolved with the text for the LLM."""
        kinds = set(kinds)
        pool = [t for t in self.targets if t.kind in kinds]
        text = str(text or "").strip().strip('"').strip()
        if text.lower().startswith("id "):
            text = text[3:].strip()
        text = text.strip("[]").strip() if text.startswith("[") and text.endswith("]") else text
        if not text:
            if len(pool) == 1:
                return pool[0]
            raise Unresolved(f"Name the {what}: there are {len(pool)} — " + self._list(pool) if pool
                             else f"HomeClaw shows no {what}.")
        if _HEX.match(text):
            up = text.upper()
            hits = [t for t in pool if t.key.startswith(up)] or [t for t in pool if t.id.startswith(up)]
            if len(hits) == 1:
                return hits[0]
            if hits:
                raise Unresolved(f"'{text}' fits several — use the ref of one: " + self._list(hits))
        hits = self._by_name(pool, text)
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise Unresolved(f"Several {what}s are called '{text}' — use the ref of one: " + self._list(hits))
        others = [t for t in self.targets if t.kind not in kinds and (t.key.startswith(text.upper()) or self._by_name([t], text))]
        if others:
            kind = others[0].kind.replace("_", " ")
            raise Unresolved(f"'{text}' is a {kind} ({others[0].label}), not a {what}.")
        raise Unresolved(f"No {what} called '{text}' in HomeClaw" + (f" — these are: {self._list(pool)}" if pool else "."))

    @staticmethod
    def _by_name(pool: list[Target], text: str) -> list[Target]:
        wanted = text.lower()
        room = None
        if match := _ROOM_SUFFIX.match(text):
            wanted, room = match.group(1).strip().lower(), match.group(2).strip().lower()
        hits = []
        for t in pool:
            names = {t.name.lower(), f"{t.room} {t.name}".lower()}
            if t.zone:
                names |= {t.zone.lower(), f"{t.name} {t.zone}".lower(), f"{t.name}, zone {t.zone}".lower()}
            if wanted in names and (room is None or t.room.lower() == room):
                hits.append(t)
        return hits

    def _list(self, targets: list[Target]) -> str:
        return "; ".join(f"{t.label} = {self.ref(t)}" for t in targets)


# --- rendering ------------------------------------------------------------------------------------

def _number(value) -> Optional[float]:
    try:
        return float(str(value).replace("°C", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _degrees(value, store: Optional[HeldValues] = None, ref: Optional[str] = None) -> str:
    number = _held(_number(value), store, ref)
    return f"{number:.1f} °C" if number is not None else str(value)


def _percent(value, store: Optional[HeldValues] = None, ref: Optional[str] = None) -> str:
    number = _held(_number(value), store, ref)
    return f"{number:.0f} %" if number is not None else str(value)


def _held(number: Optional[float], store: Optional[HeldValues], ref: Optional[str]) -> Optional[float]:
    return store.show(ref, number) if store is not None and number is not None else number


def _battery(value, held: Optional[Held], ref: Optional[str]) -> str:
    """The battery part of a line, or "" while the battery is fine: only a low one is worth a note.
    The reading decides whether it shows, the held value what it says."""
    number = _number(value)
    if number is None or number >= BATTERY_SHOWN_BELOW_PCT:
        return ""
    return f"battery {_held(number, held.battery if held else None, ref):.0f} %"


def _on(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "on", "yes")


def state_text(t: Target, held: Optional[Held] = None, ref: Optional[str] = None) -> str:
    """What the prompt line says about the target's state; unread values are left out. With `held`,
    the readings are rounded and held under `ref` (the target's ref in the index); setpoints are exact."""
    if not t.reachable:
        return "unreachable"
    s = {k: v for k, v in t.state.items() if is_read(v)}
    if t.kind == SENSOR or t.kind == THERMOSTAT:
        parts = []
        if "current_temperature" in s:
            parts.append(_degrees(s["current_temperature"], held.temperature if held else None, ref))
        if "target_temperature" in s:
            parts.append(f"target {_degrees(s['target_temperature'])}")
        if "target_heating_cooling" in s:
            parts.append(f"mode {s['target_heating_cooling']}")
        if "current_heating_cooling" in s and s.get("current_heating_cooling") != s.get("target_heating_cooling"):
            parts.append(f"now {s['current_heating_cooling']}")
        if "current_humidity" in s:
            moisture = "gardena" in t.manufacturer.lower()
            parts.append(f"{'soil moisture' if moisture else 'humidity'} "
                         f"{_percent(s['current_humidity'], held.humidity if held else None, ref)}")
        if "occupancy_detected" in s or "motion_detected" in s:
            parts.append("motion" if _on(s.get("occupancy_detected", s.get("motion_detected"))) else "no motion")
        for key, value in s.items():
            if key not in ("current_temperature", "target_temperature", "target_heating_cooling", "current_heating_cooling",
                           "current_humidity", "occupancy_detected", "motion_detected", "battery_level", "temperature_units"):
                parts.append(f"{key.replace('_', ' ')} {value}")
        if battery := _battery(s.get("battery_level"), held, ref):
            parts.append(battery)
        return ", ".join(parts) or "no reading"
    if t.kind == COVER:
        current, target = _number(s.get("current_position")), _number(s.get("target_position"))
        if current is None:
            return "position unknown"
        text = "open" if current >= 100 else "closed" if current <= 0 else f"{current:.0f} % open"
        if target is not None and target != current:
            text += f", moving to {target:.0f} %"
        return text
    if t.kind == GARAGE:
        current, target = s.get("current_door_state", "unknown"), s.get("target_door_state")
        return f"{current}" + (f", going {target}" if target and target != current else "") + \
            (", OBSTRUCTED" if _on(s.get("obstruction_detected")) else "")
    if t.kind == IRRIGATION:
        parts = ["watering" if _on(s.get("active")) else "off"]
        if battery := _battery(s.get("battery_level"), held, ref):
            parts.append(battery)
        return ", ".join(parts)
    if t.kind in SWITCHED:
        if "power" not in s:
            return "state unknown"
        if not _on(s["power"]):
            return "off"
        return f"on {_percent(s['brightness'])}" if "brightness" in s else "on"
    return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s.items()) or "no reading"


def render(index: Index, scenes: list[dict], held: Optional[Held] = None) -> str:
    """The prompt section's body: one `## Room` block per room, a line per target ending in
    its ref, then the scenes by name. `held` rounds and holds the readings (see Held)."""
    rooms: dict[str, list[Target]] = {}
    for t in index.targets:
        rooms.setdefault(t.room or "No room", []).append(t)
    order = {kind: i for i, kind in enumerate(KIND_ORDER)}
    blocks = []
    for room in sorted(rooms, key=str.lower):
        lines = [f"## {room}"]
        for t in sorted(rooms[room], key=lambda t: (order.get(t.kind, len(order)), t.name.lower(), t.zone or "", t.key)):
            name = f"{t.name}, zone {t.zone}" if t.zone else t.name
            ref = index.ref(t)
            lines.append(f"- {name} ({t.kind.replace('_', ' ')}): {state_text(t, held, ref)} [{ref}]")
        blocks.append("\n".join(lines))
    names = sorted({str(s.get("name")) for s in scenes}, key=str.lower)
    if names:
        blocks.append("## Scenes\n" + ", ".join(names))
    return "\n\n".join(blocks) if blocks else "HomeClaw lists no accessory beyond those Keep's other integrations bring."


# --- events -----------------------------------------------------------------------------------------

def event_entry(event: dict, targets: dict[str, list[Target]]) -> Optional[FeedEntry]:
    """A `characteristic_change` event as a history row, or None for any other type, an event
    without a time or one of an accessory not in `targets` (accessory UUID → its targets)."""
    if event.get("type") != "characteristic_change":
        return None
    ts = parse_time(event.get("timestamp") or "")
    accessory = event.get("accessory") or {}
    known = targets.get(str(accessory.get("id") or "").upper())
    if ts is None or not known:
        return None
    service = str(event.get("service") or "")
    target = next((t for t in known if t.zone and t.zone == service), known[0])
    characteristic = str(event.get("characteristic") or "?").replace("_", " ")
    before, after = event.get("previous_value"), event.get("value")
    change = f"{before} → {after}" if is_read(before) else f"now {after}"
    return FeedEntry(ts, f"HomeKit: {target.label} {characteristic} {change}")
