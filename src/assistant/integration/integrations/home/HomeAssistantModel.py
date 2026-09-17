"""Home Assistant's states and registries as the HomeAutomation data model, pure functions
(plans/HOME_ASSISTANT_PLAN.md §1): which domain lands in which of the four snapshot lists, how
a state reads as a value (never with parens — `to_display` would turn them into a room), which
entities the prompt sees (the ones exposed to Assist, HA's own default rule mirrored), the name
index that turns the names the LLM hands back into entity ids, and the wording of a state
change as a feed row."""
import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .HomeAutomation import Entity, HomeAutomation, HomeSnapshot
from ...Feed import FeedEntry
from common.logging import trace

LIGHT_DOMAINS = ("light", "switch", "fan", "cover", "lock")
SENSOR_DOMAINS = ("sensor", "binary_sensor", "climate", "media_player", "weather")
VARIABLE_DOMAINS = ("input_boolean", "input_number", "input_select", "input_text", "input_datetime", "person")
ACTION_DOMAINS = ("scene", "script", "automation", "button", "input_button")
SECTION_OF = {**{d: "lights" for d in LIGHT_DOMAINS}, **{d: "sensors" for d in SENSOR_DOMAINS},
              **{d: "variables" for d in VARIABLE_DOMAINS}, **{d: "actions" for d in ACTION_DOMAINS}}

# The voice assistant whose exposure list we read: Assist, the one HA's own LLM agents use.
ASSISTANT = "conversation"
# HA's default exposure (homeassistant/components/homeassistant/exposed_entities.py,
# `async_should_expose`) for an entity the user has made no choice about.
DEFAULT_EXPOSED_DOMAINS = {"climate", "cover", "fan", "humidifier", "light", "media_player", "scene",
                           "switch", "todo", "vacuum", "water_heater"}
DEFAULT_EXPOSED_BINARY_SENSOR_CLASSES = {"door", "garage_door", "lock", "motion", "opening", "presence", "window"}
DEFAULT_EXPOSED_SENSOR_CLASSES = {"aqi", "carbon_monoxide", "carbon_dioxide", "humidity", "pm10", "pm25",
                                  "temperature", "volatile_organic_compounds"}

BINARY_WORDS = {  # device class → (on, off)
    "door": ("open", "closed"), "window": ("open", "closed"), "garage_door": ("open", "closed"),
    "opening": ("open", "closed"), "motion": ("detected", "clear"), "occupancy": ("detected", "clear"),
    "presence": ("detected", "clear"), "lock": ("unlocked", "locked"),
}
BINARY_VERBS = {  # device class → (went on, went off) for the feed
    "door": ("opened", "closed"), "window": ("opened", "closed"), "garage_door": ("opened", "closed"),
    "opening": ("opened", "closed"), "motion": ("detected motion", "cleared"),
    "occupancy": ("detected occupancy", "cleared"), "presence": ("detected presence", "cleared"),
    "lock": ("unlocked", "locked"),
}
_SPACES = re.compile(r"\s+")
_PAREN_SUFFIX = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass
class Registries:
    """What `config/*_registry/list` and `homeassistant/expose_entity/list` returned, keyed by id."""
    areas: dict[str, dict] = field(default_factory=dict)        # area_id → entry
    entities: dict[str, dict] = field(default_factory=dict)     # entity_id → entry
    devices: dict[str, dict] = field(default_factory=dict)      # device id → entry
    exposed: dict[str, dict[str, bool]] = field(default_factory=dict)   # entity_id → {assistant: bool}
    expose_new: bool = True                                     # HA exposes new entities to Assist


class Unresolved(Exception):
    """A name the index cannot turn into one entity; the message is for the LLM."""


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def friendly_name(state: dict) -> str:
    name = (state.get("attributes") or {}).get("friendly_name")
    if name:
        return str(name).strip()
    return entity_id_words(state["entity_id"])


def entity_id_words(entity_id: str) -> str:
    return entity_id.split(".", 1)[-1].replace("_", " ").strip().title()


def area_of(entity_id: str, registries: Registries) -> Optional[dict]:
    """The entity's area entry: its own `area_id`, else its device's effective area (a child
    device without one inherits its parent's) — the rule of HA's `area_name()` template."""
    entry = registries.entities.get(entity_id)
    if entry is None:
        return None
    if entry.get("area_id"):
        return registries.areas.get(entry["area_id"])
    device = registries.devices.get(entry.get("device_id") or "")
    for _ in range(2):      # the device, then its parent
        if device is None:
            return None
        if device.get("area_id"):
            return registries.areas.get(device["area_id"])
        device = registries.devices.get(device.get("parent_device_id") or "")
    return None


def should_expose(state: dict, registries: Registries) -> bool:
    """Whether Assist sees the entity: the user's stored choice, else HA's default rule."""
    entity_id = state["entity_id"]
    entry = registries.entities.get(entity_id)
    if entry is not None and (entry.get("hidden_by") or entry.get("disabled_by")):
        return False
    choice = registries.exposed.get(entity_id, {}).get(ASSISTANT)
    if choice is not None:
        return choice
    if not registries.expose_new:
        return False
    if entry is not None and entry.get("entity_category"):
        return False
    domain = domain_of(entity_id)
    if domain in DEFAULT_EXPOSED_DOMAINS:
        return True
    device_class = _device_class(state, entry)
    if domain == "binary_sensor":
        return device_class in DEFAULT_EXPOSED_BINARY_SENSOR_CLASSES
    if domain == "sensor":
        return device_class in DEFAULT_EXPOSED_SENSOR_CLASSES
    return False


def _device_class(state: dict, entry: Optional[dict]) -> Optional[str]:
    attributes = state.get("attributes") or {}
    return attributes.get("device_class") or (entry or {}).get("device_class") \
        or (entry or {}).get("original_device_class")


def format_value(state: dict, entry: Optional[dict] = None) -> str:
    """The state as the snapshot's value. `unavailable` / `unknown` are shown as they are:
    the LLM should know. Never contains parens."""
    domain = domain_of(state["entity_id"])
    value = str(state.get("state", "unknown"))
    attributes = state.get("attributes") or {}
    if value in ("unavailable", "unknown") and domain not in ACTION_DOMAINS:
        return value
    if domain == "light":
        if value == "on" and attributes.get("brightness") is not None:
            return f"{_percent(attributes['brightness'])}%"
        return value
    if domain == "fan":
        if value == "on" and attributes.get("percentage") is not None:
            return f"{_int(attributes['percentage'])}%"
        return value
    if domain == "cover":
        if attributes.get("current_position") is not None:
            return f"{value} {_int(attributes['current_position'])}%"
        return value
    if domain == "sensor":
        return f"{_precise(value, entry)} {attributes.get('unit_of_measurement') or ''}".strip()
    if domain == "binary_sensor":
        words = BINARY_WORDS.get(_device_class(state, entry) or "")
        if words and value in ("on", "off"):
            return words[0] if value == "on" else words[1]
        return value
    if domain == "climate":
        parts = [value]
        if attributes.get("current_temperature") is not None:
            parts.append(str(attributes["current_temperature"]))
        if attributes.get("temperature") is not None:
            parts.append(f"target {attributes['temperature']}")
        return ", ".join(parts)
    if domain == "media_player":
        if value == "playing" and attributes.get("media_title"):
            title = str(attributes["media_title"])
            if attributes.get("media_artist"):
                title += f" – {attributes['media_artist']}"
            return f"playing: {title}"
        return value
    if domain == "weather":
        if attributes.get("temperature") is not None:
            return f"{value}, {attributes['temperature']} {attributes.get('temperature_unit') or ''}".strip()
        return value
    if domain == "automation":
        return f"automation, {value}"
    if domain in ("scene", "script"):
        return domain
    if domain in ("button", "input_button"):
        return "button"
    return value


def _percent(brightness) -> int:
    """HA's 0–255 as 1–100: a lit lamp is never 0%."""
    return max(1, min(100, round(float(brightness) * 100 / 255)))


def _int(value) -> int:
    return int(round(float(value)))


def _precise(value: str, entry: Optional[dict]) -> str:
    """A numeric sensor state at the entity's `display_precision` when the user set one."""
    precision = ((entry or {}).get("options") or {}).get("sensor", {}).get("display_precision")
    if precision is None or not _NUMBER.match(value):
        return value
    return f"{float(value):.{int(precision)}f}"


class NameIndex:
    """Name → entity_id for the commands. Every entity is reachable as `name (area)` (what
    `from_display` hands the backend for the `"name [Area]"` the prompt showed), as its bare
    name when that is unique, and under each registry alias the same two ways."""

    AMBIGUOUS = None

    def __init__(self):
        self._ids: dict[str, Optional[str]] = {}       # normalized key → entity_id, None when ambiguous
        self._holders: dict[str, list[str]] = {}       # normalized key → every entity_id behind it
        self._domains: dict[str, str] = {}
        self._display: dict[str, str] = {}

    def add(self, entity_id: str, name: str, area: Optional[str], aliases: tuple[str, ...] = (),
            area_aliases: tuple[str, ...] = ()):
        self._domains[entity_id] = domain_of(entity_id)
        self._display[entity_id] = HomeAutomation.to_display(f"{name} ({area})" if area else name)
        for label in (name, *aliases):
            self._put(label, entity_id, area)
            for place in ((area, *area_aliases) if area else ()):
                self._put(f"{label} ({place})", entity_id, area)

    def _put(self, key: str, entity_id: str, area: Optional[str]):
        key = normalize(key)
        holders = self._holders.setdefault(key, [])
        if entity_id not in holders:
            holders.append(entity_id)
        self._ids[key] = entity_id if len(holders) == 1 else self.AMBIGUOUS

    def resolve(self, name: str) -> str:
        """The entity id behind *name*, else Unresolved with the text for the LLM."""
        key = normalize(name)
        if not key:
            raise Unresolved("No entity name given.")
        for candidate in self._candidates(key):
            if candidate in self._ids:
                entity_id = self._ids[candidate]
                if entity_id is not None:
                    return entity_id
                options = ", ".join(self._display[i] for i in self._holders[candidate])
                raise Unresolved(f'"{name}" could be {options} — say which.')
        close = difflib.get_close_matches(key, [k for k, v in self._ids.items() if v is not None], n=3, cutoff=0.6)
        suggestions = sorted({self._display[self._ids[k]] for k in close})
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise Unresolved(f'No entity named "{name}".{hint}')

    @staticmethod
    def _candidates(key: str) -> list[str]:
        """The key itself, then — for `name (place)` — the bare name: the LLM may have
        invented the place or translated it."""
        match = _PAREN_SUFFIX.match(key)
        if match and match.group(1):
            return [key, match.group(1).strip()]
        return [key]

    def domain(self, entity_id: str) -> str:
        return self._domains.get(entity_id) or domain_of(entity_id)

    def display(self, entity_id: str) -> Optional[str]:
        """`Name [Area]`, what the prompt shows for the entity; None for one not indexed."""
        return self._display.get(entity_id)

    def __len__(self):
        return len(self._domains)

    def __contains__(self, entity_id: str) -> bool:
        return entity_id in self._domains


def normalize(text: str) -> str:
    text = HomeAutomation.from_display(text)
    return _SPACES.sub(" ", text).strip().lower()


def build(states: dict[str, dict], registries: Registries, exposed_only: bool) -> tuple[HomeSnapshot, NameIndex]:
    """The four lists, sorted by name within each, plus the index of everything listed. An
    entity whose name and area another entity already has gets its domain appended (`Kitchen
    switch` beside `Kitchen light`), else its object id, so no two lines read the same; the
    plain name still resolves to "could be ... — say which"."""
    snapshot = HomeSnapshot()
    index = NameIndex()
    dropped_domain = dropped_exposure = 0
    chosen: list[tuple[str, str, Optional[dict], dict]] = []     # (section, name, area entry, state)
    for entity_id in sorted(states):
        state = states[entity_id]
        section = SECTION_OF.get(domain_of(entity_id))
        if section is None:
            dropped_domain += 1
            continue
        if exposed_only and not should_expose(state, registries):
            dropped_exposure += 1
            continue
        chosen.append((section, friendly_name(state), area_of(entity_id, registries), state))
    names = _disambiguate(chosen)
    for (section, original, area, state), name in zip(chosen, names):
        entity_id = state["entity_id"]
        entry = registries.entities.get(entity_id) or {}
        area_name = area.get("name") if area else None
        getattr(snapshot, section).append(Entity(name, format_value(state, entry), area_name))
        aliases = tuple(entry.get("aliases") or ()) + ((original,) if name != original else ())
        index.add(entity_id, name, area_name, aliases, tuple((area or {}).get("aliases") or ()))
    for section in ("lights", "sensors", "variables", "actions"):
        getattr(snapshot, section).sort(key=lambda e: (e.name.lower(), e.area or ""))
    trace(f"Home Assistant: {len(index)} entities in the snapshot ({len(snapshot.lights)} lights, "
          f"{len(snapshot.sensors)} sensors, {len(snapshot.variables)} variables, {len(snapshot.actions)} "
          f"actions); dropped {dropped_domain} by domain, {dropped_exposure} not exposed to Assist")
    return snapshot, index


def _disambiguate(chosen: list[tuple[str, str, Optional[dict], dict]]) -> list[str]:
    seen: dict[tuple[str, str], list[int]] = {}
    for i, (_, name, area, _state) in enumerate(chosen):
        seen.setdefault((name.lower(), (area or {}).get("name") or ""), []).append(i)
    names = [name for _, name, _, _ in chosen]
    for indexes in seen.values():
        if len(indexes) < 2:
            continue
        domains = [domain_of(chosen[i][3]["entity_id"]) for i in indexes]
        for i, domain in zip(indexes, domains):
            suffix = domain if domains.count(domain) == 1 else entity_id_words(chosen[i][3]["entity_id"]).lower()
            names[i] = f"{names[i]} {suffix}"
            trace(f"Home Assistant: '{chosen[i][1]}' is not unique — listed as '{names[i]}'")
    return names


def feed_entry(old: Optional[dict], new: Optional[dict], index: NameIndex, user: Optional[str] = None) -> Optional[FeedEntry]:
    """A state change as a history row, or None for one the dialog does not want: attribute-only
    changes, numeric sensors ticking, entities outside the snapshot's lists, an entity
    appearing or vanishing."""
    if old is None or new is None:
        return None
    entity_id = new["entity_id"]
    domain = domain_of(entity_id)
    if domain in ("sensor", "climate", "media_player", "weather") or domain not in SECTION_OF:
        return None
    if index and entity_id not in index:
        return None
    verb = _verb(domain, old, new)
    if verb is None:
        return None
    display = index.display(entity_id) if index else None
    if display is None:
        display = HomeAutomation.to_display(friendly_name(new))
    if domain in ACTION_DOMAINS and "[" not in display:
        display = f"{display} [{domain}]"
    content = f"{display} {verb}"
    if user:
        content += f" by {user}"
    return FeedEntry(_timestamp(new), content)


def _verb(domain: str, old: dict, new: dict) -> Optional[str]:
    before, after = str(old.get("state")), str(new.get("state"))
    attributes = new.get("attributes") or {}
    if domain in ("automation", "script", "scene"):
        if domain == "scene":
            return "activated" if before != after and after not in ("unknown", "unavailable") else None
        if (attributes.get("last_triggered") or None) != ((old.get("attributes") or {}).get("last_triggered") or None) \
                and attributes.get("last_triggered"):
            return "triggered" if domain == "automation" else "ran"
        if domain == "automation" and before != after and after in ("on", "off"):
            return "enabled" if after == "on" else "disabled"
        return None
    if before == after:
        return None
    if after == "unavailable":
        return "became unavailable"
    if after == "unknown":
        return None
    if domain == "light":
        if after == "on":
            return f"turned on {_percent(attributes['brightness'])}%" if attributes.get("brightness") is not None else "turned on"
        return "turned off" if after == "off" else after
    if domain == "fan":
        if after == "on" and attributes.get("percentage") is not None:
            return f"turned on {_int(attributes['percentage'])}%"
        return "turned on" if after == "on" else "turned off" if after == "off" else after
    if domain in ("switch", "input_boolean"):
        return "turned on" if after == "on" else "turned off" if after == "off" else after
    if domain == "cover":
        return {"open": "opened", "closed": "closed", "opening": "is opening", "closing": "is closing"}.get(after, after)
    if domain == "lock":
        return {"locked": "locked", "unlocked": "unlocked", "jammed": "jammed",
                "locking": "is locking", "unlocking": "is unlocking"}.get(after, after)
    if domain == "binary_sensor":
        verbs = BINARY_VERBS.get(attributes.get("device_class") or "")
        if verbs and after in ("on", "off"):
            return verbs[0] if after == "on" else verbs[1]
        return "turned on" if after == "on" else "turned off" if after == "off" else after
    if domain == "person":
        if after == "home":
            return "arrived home"
        if after == "not_home":
            return "left home"
        return f"is at {after}"
    if domain in ("button", "input_button"):
        return "pressed"
    return f"changed to {after}"


def _timestamp(state: dict) -> float:
    """When the change the row describes happened. last_updated moves with every change,
    last_changed only with a state change — an automation that fires while staying "on"
    moves last_triggered and last_updated alone, and stamping its rows with last_changed
    would give every trigger the same timestamp (and the same history dedup key)."""
    for key in ("last_updated", "last_changed"):
        value = state.get(key)
        if value:
            try:
                return datetime.fromisoformat(value).timestamp()
            except ValueError:
                pass
    return datetime.now().timestamp()
