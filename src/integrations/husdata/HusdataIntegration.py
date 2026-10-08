"""The heat pump through a Husdata H60 or H66 gateway: the common registers the gateway
reports, decoded by the type digit, and the two settings the assistant writes
(https://husdata.se/docs/h60-manual/api-integration/).

The gateway speaks plain HTTP on the LAN. `GET /api/alldata` is every register as
`{"0001": 275, ...}`: a four-hex-digit id whose first digit is the data type (0 temperature,
1 on/off, 2 number, 3 percent, ... — `TYPE_SCALE`) and a signed 16-bit value, tenths for the
scaled types. `GET /api/set?idx=&val=` writes one register the same way; it needs
`API_ENABLED=2` in the gateway's web UI (the config fragment tells the user). `/api/status`
answers the health check with `hpconn`. Which registers exist depends on the pump family
behind the gateway, so a snapshot carries only what the gateway reported and a write to a
register the pump never reported is refused before it goes out."""
from dataclasses import dataclass
from typing import List, Optional

import requests

from assistant.integration.Integration import Integration
from common.ConfigManager import config
from common.logging import info, trace

TIMEOUT = 10
# Type digit → (unit, divide by ten); from the H1 register manual the gateways inherit.
TYPE_SCALE = {"0": ("°C", True), "1": ("", False), "2": ("", True), "3": ("%", True), "4": ("A", True),
              "5": ("kWh", True), "6": ("h", False), "7": ("min", False), "8": ("DM", False), "9": ("kW", False)}
ROOM_TARGET = "0203"
HOT_WATER_SETPOINT = "0111"
# The common registers, in the order the prompt section lists them. A pump reports a subset.
TEMPERATURES = (("0007", "Outdoor"), ("0008", "Indoor"), ("0021", "Indoor 2"), (ROOM_TARGET, "Room target"),
                ("0009", "Hot water"), ("000A", "Hot water mid"), (HOT_WATER_SETPOINT, "Hot water setpoint"),
                ("0002", "Radiator forward"), ("0001", "Radiator return"), ("0107", "Heating setpoint"),
                ("0004", "Heat carrier forward"), ("0003", "Heat carrier return"),
                ("0005", "Brine in"), ("0006", "Brine out"), ("000B", "Hot gas"), ("000C", "Suction gas"),
                ("000E", "Air intake"), ("000F", "Exhaust air"), ("0011", "Pool"))
STATES = (("1A01", "Compressor"), ("1A02", "Additional heat step 1"), ("1A03", "Additional heat step 2"),
          ("1A04", "Brine pump"), ("1A05", "Heat carrier pump"), ("1A06", "Radiator pump"),
          ("1A07", "Switch valve"), ("1A09", "Fan"))
OTHERS = (("3108", "Compressor speed"), ("2105", "Degree minutes"), ("3104", "Additional heat"),
          ("9104", "Additional heat"), ("6F63", "Compressor hours heating"), ("6F64", "Compressor hours hot water"))
ALARMS = ("2A20", "1A20")       # the H1 manual's alarm number, the newer firmware's alarm flag


class HusdataError(Exception):
    """The gateway could not be reached or refused the request; the text is for the LLM."""


@dataclass(frozen=True)
class Register:
    idx: str
    name: str
    value: float | int
    unit: str

    def __str__(self) -> str:
        if self.idx.startswith("1"):
            return f"{self.name} {'on' if self.value else 'off'}"
        number = f"{self.value:.1f}" if isinstance(self.value, float) else str(self.value)
        return f"{self.name} {number}{' ' + self.unit if self.unit else ''}"


def decode(idx: str, raw: int) -> Register | None:
    """A register's reading, or None for a value the gateway reports as unavailable (the
    Homey app shows those as "-"): the unsigned 16-bit wire value made signed, scaled by the
    type digit."""
    unit, tenths = TYPE_SCALE.get(idx[0].upper(), ("", False))
    value = raw - 65536 if raw > 32767 else raw
    if value == -32768:
        return None
    return Register(idx, idx, value / 10 if tenths else value, unit)


def to_wire(celsius: float) -> int:
    return int(round(celsius * 10))


class HusdataIntegration(Integration):
    """The heat pump via a Husdata H60/H66 gateway: readings and setpoints.

    Shows the heat pump's temperatures, running state and alarm in the prompt, and sets the
    room target and the hot water setpoint when asked.

    One `alldata` read per poll, cached as the last snapshot; the two writes refuse a register
    the pump has not reported. Nothing beyond the gateway's own HTTP API."""

    @property
    def name(self) -> str:
        return "Husdata"

    @property
    def config_keys(self) -> List[str]:
        return ["husdata.host"]

    def __init__(self):
        super().__init__()
        self._registers: dict[str, int] = {}

    @property
    def base_url(self) -> str:
        return f"http://{config.get('husdata.host')}"

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        try:
            response = requests.get(f"{self.base_url}{path}", params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.Timeout:
            raise HusdataError("The Husdata gateway did not answer in time")
        except requests.ConnectionError:
            raise HusdataError(f"Could not connect to the Husdata gateway at {config.get('husdata.host')}")
        except ValueError:
            raise HusdataError("The Husdata gateway answered with something that is not JSON "
                               "— is API_ENABLED set in its web UI?")

    def check_health(self) -> bool:
        status = self._get("/api/status").get("status", {})
        if not status.get("hpconn"):
            raise HusdataError("The gateway is up but reports no connection to the heat pump")
        return True

    # --- reading -----------------------------------------------------------------------------

    def refresh(self) -> dict[str, int]:
        """Every register the gateway reports, raw; kept as the last snapshot."""
        data = self._get("/api/alldata")
        registers = {}
        for idx, raw in data.items():
            try:
                registers[str(idx).upper()] = int(raw)
            except (TypeError, ValueError):
                trace(f"Husdata: register {idx} is not a number ({raw!r}), skipped")
        if not registers:
            raise HusdataError("The gateway reported no registers — is the heat pump connected?")
        self._registers = registers
        trace(f"Husdata: {len(registers)} registers read")
        return registers

    def has_register(self, idx: str) -> bool:
        return idx in self._registers

    def reading(self, idx: str, name: str) -> Register | None:
        raw = self._registers.get(idx)
        if raw is None:
            return None
        register = decode(idx, raw)
        return Register(idx, name, register.value, register.unit) if register else None

    def snapshot(self) -> str:
        """The prompt section's body, from a fresh read: the temperatures, the running state,
        the counters and the alarm the pump reports, one group per line."""
        self.refresh()
        temperatures = [str(r) for idx, name in TEMPERATURES if (r := self.reading(idx, name))]
        states = [str(r) for idx, name in STATES if (r := self.reading(idx, name))]
        others = [str(r) for idx, name in OTHERS if (r := self.reading(idx, name))]
        lines = [", ".join(group) for group in (temperatures, states, others) if group]
        lines.append(self._alarm_line())
        return "\n".join(lines)

    def _alarm_line(self) -> str:
        for idx in ALARMS:
            raw = self._registers.get(idx)
            if raw is not None:
                return "Alarm: none" if raw == 0 else f"ALARM active (code {raw})"
        return "Alarm: not reported by this pump"

    # --- writing -----------------------------------------------------------------------------

    def set_room_target(self, celsius: float) -> str:
        return self._set_temperature(ROOM_TARGET, "room target", celsius, config.get("husdata.room_target_range", [15, 25]))

    def set_hot_water(self, celsius: float) -> str:
        return self._set_temperature(HOT_WATER_SETPOINT, "hot water setpoint", celsius, config.get("husdata.hot_water_range", [40, 60]))

    def _set_temperature(self, idx: str, what: str, celsius: float, allowed: list) -> str:
        """Writes `celsius` to the register and says what the pump accepted; raises
        HusdataError for a value outside the configured range, a register the pump does not
        expose or a refusal from the gateway."""
        low, high = float(allowed[0]), float(allowed[1])
        if not low <= celsius <= high:
            raise HusdataError(f"{celsius:g} °C is outside the allowed {what} range {low:g}–{high:g} °C "
                               f"(husdata.{'room_target_range' if idx == ROOM_TARGET else 'hot_water_range'} in config.yaml)")
        if not self._registers:
            self.refresh()
        if not self.has_register(idx):
            raise HusdataError(f"This heat pump does not expose the {what} (register {idx}) through the gateway")
        info(f"Husdata: setting {what} to {celsius:g} °C (register {idx})")
        answer = self._get("/api/set", {"idx": idx, "val": to_wire(celsius)}).get("response", "")
        if answer != "Ok":
            raise HusdataError(f"The gateway refused to set the {what}: {answer or 'no response'} "
                               f"(writes need API_ENABLED=2 in the gateway's web UI, then a restart of the gateway)")
        self._registers[idx] = to_wire(celsius)
        return f"The {what} is now {celsius:g} °C (the gateway confirmed the write)"
