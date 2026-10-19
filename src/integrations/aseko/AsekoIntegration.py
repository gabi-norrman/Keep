from typing import Any, Dict, List, Optional, Tuple

import requests

from assistant.integration.Integration import Integration
from assistant.prompt.Held import HeldValues
from common.ConfigManager import config
from common.Version import version
from common.logging import info, trace

API_BASE = "https://api.aseko.cloud/api/v1"
CLIENT_NAME = "Keep"                # X-Client-Name: what Aseko sees this integration as
TIMEOUT = 15
PAGE_LIMIT = 100                    # the API's largest page; nobody pairs more units than that
ACCOUNT_URL = "https://account.aseko.cloud/"
API_KEYS_URL = "https://account.aseko.cloud/profile/settings/api-keys"

# What a refused call means for whoever must fix it, by the API's errorType
ERROR_HINTS = {
    "TOS_NOT_ACCEPTED": f"the Aseko account has not accepted the latest terms of service — do that at {ACCOUNT_URL}",
    "UNPAID_OR_LOW_SUBSCRIPTION_PLAN": "the Aseko account's plan does not include API access",
    "API_KEY_INVALID": f"aseko.api_key is not a key Aseko knows — make one at {API_KEYS_URL}",
    "API_KEY_EXPIRED": f"aseko.api_key has expired — make a new one at {API_KEYS_URL}",
    "API_KEY_DELETED": f"aseko.api_key was deleted on the account — make a new one at {API_KEYS_URL}",
    "API_KEY_MISSING": "aseko.api_key is empty",
}

Reading = Tuple[str, str]           # a label and its value, as the prompt and the tab show it


class AsekoError(Exception):
    """A call the API refused, with the hint for whoever must fix it."""


class AsekoIntegration(Integration):
    """The pool's water readings and warnings, read from Aseko Cloud.

    Reads what the pool unit measures — water temperature, pH, redox, chlorine, salinity,
    filtration, heating, water level — and the unit's own warnings, so Keep can say how the pool
    is doing and keep an eye on a value you ask it to watch. The public API is read-only: it
    cannot change anything.

    Three endpoints (api.aseko.cloud/api/v1/docs): the key check, the paired units and one
    unit's detail. The section polls the detail of every unit every few minutes and the tab
    shows the last fetch (`last_units`). A reading is `statusValues[...]`, absent when the unit
    lacks the sensor and null when it has nothing to say; `readings` prints only what is there.
    The key is read from config on every call, so a `config_request` takes effect at once."""

    @property
    def name(self) -> str:
        return "Aseko Pool"

    @property
    def config_keys(self) -> List[str]:
        return ["aseko.api_key"]

    def __init__(self, session: Optional[requests.Session] = None):
        super().__init__()
        self._session = session or requests.Session()
        self.last_units: List[Dict[str, Any]] = []
        self.held = PoolHeld()      # the section's rounded-and-held readings, across fetches

    def check_health(self) -> bool:
        return isinstance(self._get("/paired-units", params={"limit": PAGE_LIMIT}).get("items"), list)

    def fetch_units(self) -> List[Dict[str, Any]]:
        """Every unit paired to the account with its detail, remembered for the tab."""
        items = self._get("/paired-units", params={"limit": PAGE_LIMIT}).get("items") or []
        if not items:
            info("Aseko: no pool unit is paired to the account")
        units = [self._get(f"/paired-units/{item['serialNumber']}") for item in items]
        trace(f"Aseko: {len(units)} unit(s) fetched: "
              + ", ".join(f"{unit_name(u)} ({'online' if u.get('online') else 'offline'})" for u in units))
        self.last_units = units
        return units

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {config.get('aseko.api_key', '')}",
                "X-Client-Name": CLIENT_NAME, "X-Client-Version": version()}

    def _get(self, path: str, params: Optional[dict] = None) -> Dict[str, Any]:
        response = self._session.get(API_BASE + path, headers=self._headers(), params=params, timeout=TIMEOUT)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            raise AsekoError("Aseko rate limit hit (429) — the readings refresh on the next poll")
        try:
            body = response.json()
        except ValueError:
            body = {}
        error_type = body.get("errorType", "")
        message = body.get("error") or response.reason or ""
        hint = ERROR_HINTS.get(error_type)
        raise AsekoError(f"Aseko {path} answered {response.status_code} {error_type}: {message}".rstrip(": ")
                         + (f" — {hint}" if hint else ""))


def unit_name(unit: dict) -> str:
    return unit.get("name") or unit.get("serialNumber") or "Pool"


def number(value) -> str:
    """A reading without a trailing .0: 28.0 → 28, 7.21 → 7.21."""
    return format(value, "g") if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def _with_target(value, target, unit: str) -> str:
    text = f"{number(value)}{unit}"
    return f"{text} (target {number(target)}{unit})" if target is not None else text


def _on_off(running) -> str:
    return "running" if running else "off"


# The probes report to the hundredth and every changed line costs a note in the history window, so
# the prompt shows the measured values rounded to a step and held until the fresh one is a full step
# away (Held); the targets are setpoints and stay exact.
STEPS = {"waterTemperature": 1, "airTemperature": 1, "redox": 10, "ph": 0.1, "clFree": 0.1, "filterPressure": 0.1}


class PoolHeld:
    """The held readings of every unit, one store per measured field, keyed by the unit; the
    integration owns one and the section hands it to `render_units`."""

    def __init__(self):
        self._stores = {name: HeldValues(step) for name, step in STEPS.items()}

    def show(self, key, name: str, value):
        store = self._stores.get(name)
        if store is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        return store.show((key, name), value)


def readings(values: dict, held: Optional[PoolHeld] = None, key=None) -> List[Reading]:
    """The unit's status values as (label, value) pairs in a fixed order, only the ones the
    unit reports: a sensor it lacks is absent, one with nothing to say is null, both are skipped.
    With `held`, the measured values are rounded and held under `key` (the unit)."""
    v = {k: val for k, val in (values or {}).items() if val is not None}
    out: List[Reading] = []

    def put(label: str, text: Optional[str]):
        if text:
            out.append((label, text))

    def measured(name: str):
        return held.show(key, name, v[name]) if held is not None else v[name]

    if "waterTemperature" in v:
        put("Water", _with_target(measured("waterTemperature"), v.get("waterTemperatureRequired"), "°C"))
    if "airTemperature" in v:
        put("Air", f"{number(measured('airTemperature'))}°C")
    if "ph" in v:
        put("pH", _with_target(measured("ph"), v.get("phRequired"), ""))
    if "redox" in v:
        put("Redox", _with_target(measured("redox"), v.get("redoxRequired"), " mV"))
    if "clFree" in v:
        unit = v.get("clFreeRequiredUnit") or "mg/l"
        put("Free chlorine", _with_target(measured("clFree"), v.get("clFreeRequired"), f" {unit}"))
    if "clBounded" in v:
        put("Bound chlorine", f"{number(v['clBounded'])} mg/l")
    if "salinity" in v:
        put("Salinity", f"{number(v['salinity'])} kg/m³")
    if "electrolyzerRunning" in v:
        parts = [_on_off(v["electrolyzerRunning"])]
        if v["electrolyzerRunning"] and "electrodePower" in v:
            parts.append(f"{number(v['electrodePower'])} g/h")
        if v["electrolyzerRunning"] and "electrolyzerDirection" in v:
            parts.append(str(v["electrolyzerDirection"]).lower())
        put("Electrolyzer", ", ".join(parts))
    if "filtrationRunning" in v:
        put("Filtration", _filtration(v))
    if "filterPressure" in v or "filterFlowSpeed" in v:
        parts = [f"{number(measured('filterPressure'))} bar" if "filterPressure" in v else "",
                 f"{number(v['filterFlowSpeed'])} m³/h" if "filterFlowSpeed" in v else ""]
        put("Filter", ", ".join(p for p in parts if p))
    if "heatingRunning" in v:
        put("Heating", _on_off(v["heatingRunning"]))
    if "solarRunning" in v or "solarTemperature" in v:
        parts = [_on_off(v["solarRunning"]) if "solarRunning" in v else "",
                 f"{number(v['solarTemperature'])}°C" if "solarTemperature" in v else ""]
        put("Solar", ", ".join(p for p in parts if p))
    if "poolFlow" in v:
        put("Pool flow", str(v["poolFlow"]).lower())
    if "waterLevelState" in v or "waterLevel" in v:
        parts = [_level_state(v["waterLevelState"]) if "waterLevelState" in v else "",
                 f"{number(v['waterLevel'])} cm" if "waterLevel" in v else "",
                 "filling" if v.get("waterFillingRunning") else ""]
        put("Water level", ", ".join(p for p in parts if p))
    if v.get("waterFlowToProbes") is False:
        put("Probes", "no water flow — the readings cannot be taken")
    if "mode" in v:
        put("Mode", str(v["mode"]))
    return out


def _filtration(v: dict) -> str:
    text = "running" if v["filtrationRunning"] else "off"
    if v["filtrationRunning"] and v.get("filtrationSpeed"):
        text += f" at {str(v['filtrationSpeed']).lower()}"
    period = v.get("upcomingFiltrationPeriod") or {}
    if period.get("isNonstop"):
        text += ", nonstop"
    elif period.get("start") and period.get("end"):
        window = f"{period['start']}–{period['end']}"
        text += f", next {window}" if period.get("isNext") else f", period {window}"
    return text


def _level_state(state) -> str:
    state = str(state)
    return f"{state} (alarm)" if state in ("LOW", "HIGH") else state


def warnings(unit: dict) -> List[dict]:
    """The unit's status messages, errors first."""
    rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    return sorted(unit.get("statusMessages") or [], key=lambda m: rank.get(m.get("severity"), 3))


def render_unit(unit: dict, held: Optional[PoolHeld] = None) -> str:
    """One unit for the prompt: a headline, a line per reading, then the unit's own messages."""
    name = unit_name(unit)
    values = unit.get("statusValues")
    if values is None:
        return f"{name}: never connected to Aseko Cloud, no readings"
    online = "online" if unit.get("online") else "OFFLINE — these readings are the last it sent"
    lines = [f"{name}: {online}"]
    lines += [f"  {label}: {text}" for label, text in readings(values, held, unit.get("serialNumber") or name)]
    messages = warnings(unit)
    if not messages:
        lines.append("  Unit messages: none")
    else:
        lines.append("  Unit messages:")
        for m in messages:
            detail = f" — {m['detail']}" if m.get("detail") else ""
            lines.append(f"    {m.get('severity', 'INFO')}: {m.get('message') or m.get('type', '')}{detail}")
    return "\n".join(lines)


def render_units(units: List[dict], held: Optional[PoolHeld] = None) -> str:
    if not units:
        return "No pool unit is paired to the Aseko account."
    return "\n\n".join(render_unit(unit, held) for unit in units)
