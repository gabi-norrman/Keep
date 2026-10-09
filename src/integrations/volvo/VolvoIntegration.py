from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import threading
import time

import requests

from assistant.integration.Integration import Integration
from assistant.integration.OAuthPaste import SignIn
from assistant.integration.VehicleProvider import VehicleError, VehicleProvider, VehicleSpec, VehicleState
from common.ConfigManager import ConfigManager
from common.logging import error, info, trace
from integrations.volvo.VolvoAuth import (NotSignedIn, VolvoAuth, delete_token_file, not_signed_in_hint,
                                          sign_in_again_hint, token_path)

SECTION = "volvo"
API_KEY_KEY = f"{SECTION}.vcc_api_key"
CLIENT_ID_KEY = f"{SECTION}.oauth.client_id"
CLIENT_SECRET_KEY = f"{SECTION}.oauth.client_secret"
VEHICLES_KEY = f"{SECTION}.vehicles"

API_URL = "https://api.volvocars.com"
CONNECTED = f"{API_URL}/connected-vehicle/v2/vehicles"
ENERGY = f"{API_URL}/energy/v2/vehicles"
LOCATION = f"{API_URL}/location/v1/vehicles"
TIMEOUT = 15
RETRIES = 2
STATE_TTL = 60                 # the prompt section may ask every minute while a car drives: one set of reads per minute per car
COMMAND_OK = ("COMPLETED", "DELIVERED")

# The car's reachability as the command-accessibility endpoint says it, in the contract's words
CONNECTIVITY = {"AVAILABLE": "online", "CAR_IN_USE": "online", "NO_INTERNET": "offline",
                "POWER_SAVING_MODE": "asleep", "OTA_INSTALLATION_IN_PROGRESS": "updating"}
BATTERY_FUEL_TYPES = ("ELECTRIC", "PETROL/ELECTRIC", "NONE")
COMBUSTION_FUEL_TYPES = ("DIESEL", "PETROL", "PETROL/ELECTRIC")


class VolvoApiError(VehicleError):
    """A Volvo Cars API call that failed; the message is for the LLM."""


class VolvoRateLimited(VolvoApiError):
    """HTTP 429: the app's daily or per-minute quota is spent."""


class VolvoCommandError(VolvoApiError):
    """A command the car did not carry out: the invoke status and Volvo's reason."""


class VolvoIntegration(Integration, VehicleProvider):
    """The Volvos through the Volvo Cars API: state and climate.

    Shows where each Volvo is, whether it is in use and how much charge or range it has, and
    starts or stops its climate system; the household publishes its own app on Volvo's
    developer portal once and signs in with its Volvo ID from the chat or the console's tab.

    A VehicleProvider over the Connected Vehicle API v2 (vehicle details, engine status,
    statistics, fuel, command accessibility, the climatization commands), the Energy API v2
    (battery level, electric range) and the Location API; a car's handle is its VIN. Every
    call carries the household's VCC API key (`volvo.vcc_api_key`) and a bearer token from
    `VolvoAuth`, the sign-in of the app whose client id and secret are `volvo.oauth.*`. A
    car's composed state is cached for STATE_TTL seconds and its details (fuel type) for the
    process, since the app has 10,000 calls a day. Volvo offers no defrost, trunk or
    navigation command, so those three stay the contract's Unsupported. `needs_2fa` is "the
    owner has to sign in": the monitor shows NEEDS_2FA with the login hint until the paste
    flow completes. Config is read at call time."""
    login_hint = "volvo_login() from the chat, or the console's Volvo tab"

    def __init__(self, session: Optional[requests.Session] = None):
        Integration.__init__(self)
        self._session = session or requests.Session()
        self.auth = VolvoAuth(self._client_settings)
        self._details: Dict[str, dict] = {}
        self._states: Dict[str, tuple[float, VehicleState]] = {}
        self._lock = threading.RLock()

    @property
    def name(self) -> str:
        return "Volvo"

    @property
    def config_keys(self) -> List[str]:
        return [API_KEY_KEY, CLIENT_ID_KEY, CLIENT_SECRET_KEY, VEHICLES_KEY]

    # --- config --------------------------------------------------------------------------------

    @staticmethod
    def _config(key: str, default=None):
        return ConfigManager.get_instance().get(key, default)

    @classmethod
    def _client_settings(cls) -> tuple[str, str]:
        return str(cls._config(CLIENT_ID_KEY, "") or ""), str(cls._config(CLIENT_SECRET_KEY, "") or "")

    def vehicles(self) -> List[VehicleSpec]:
        """The configured cars: `name` is the vehicle_id, the VIN the handle, the display
        name an alias (and the label when set)."""
        entries = self._config(VEHICLES_KEY, []) or []
        specs: List[VehicleSpec] = []
        for entry in entries:
            if not isinstance(entry, dict):
                error(f"Volvo: invalid vehicle configuration entry: {entry}")
                continue
            name, vin, display_name = entry.get("name"), entry.get("vin"), entry.get("display_name")
            specs.append(VehicleSpec(
                id=name or display_name or vin or "car",
                label=display_name or name or vin or "car",
                handle=vin,
                aliases=tuple(a for a in (display_name, vin) if a),
                is_default=bool(entry.get("default", False)),
            ))
        if not specs:
            info("Volvo: volvo.vehicles lists no car")
        return specs

    @staticmethod
    def _require_vin(spec: VehicleSpec) -> str:
        if not spec.handle:
            raise VolvoApiError(f"Vehicle '{spec.label}' is missing its VIN in configuration.")
        return spec.handle

    # --- the sign-in, for the commands and the tab ---------------------------------------------

    def signed_in(self) -> bool:
        return self.auth.signed_in()

    def login_begin(self) -> SignIn:
        return self.auth.begin()

    def login_finish(self, pasted: str) -> str:
        """"" when the pasted answer signed the Volvo ID in, else why not."""
        failure = self.auth.finish(pasted)
        if not failure:
            with self._lock:
                self._states.clear()
        return failure

    def sign_out(self) -> None:
        self.auth.sign_out()
        with self._lock:
            self._states.clear()

    def needs_2fa(self) -> bool:
        """The owner has to sign in (never did, or the refresh was refused): local state only."""
        return not self.auth.signed_in() or self.auth.needs_sign_in_again()

    def get_auth_status(self) -> dict:
        """The console tab's summary of the sign-in."""
        client_id, client_secret = self._client_settings()
        if not client_id or not client_secret or not self._config(API_KEY_KEY):
            return {"status": "error", "message": "volvo.vcc_api_key, volvo.oauth.client_id and client_secret are needed"}
        if not self.auth.signed_in():
            return {"status": "needs_auth", "message": "Sign-in needed — Ctrl-L shows the link, Ctrl-E takes the pasted address"}
        if self.auth.needs_sign_in_again():
            return {"status": "needs_auth", "message": f"The sign-in lapsed: {self.auth.refresh_error} — Ctrl-L signs in again"}
        return {"status": "authenticated", "message": f"Signed in; {len(self.vehicles())} car(s) configured"}

    @classmethod
    def wipe_targets(cls) -> list:
        return [(token_path(), "the Volvo sign-in (its OAuth tokens)")]

    @classmethod
    def log_out(cls) -> Optional[str]:
        return "Volvo: token file deleted" if delete_token_file() else None

    def on_not_configured(self):
        delete_token_file()

    # --- health ----------------------------------------------------------------------------------

    def check_health(self) -> bool:
        specs = self.vehicles()
        if not specs or not specs[0].handle:
            info("Volvo: no car with a VIN to check")
            return False
        if not self.auth.signed_in():
            raise NotSignedIn(not_signed_in_hint())
        details = self._vehicle_details(specs[0].handle)
        return bool(details.get("vin"))

    # --- the API -------------------------------------------------------------------------------

    def _request(self, method: str, url: str, body: Optional[dict] = None) -> dict:
        """One call with the bearer token and the API key: the JSON body, {} for a 404 (an
        endpoint the car or region lacks), a VehicleError for everything else."""
        api_key = str(self._config(API_KEY_KEY, "") or "")
        headers = {"Authorization": f"Bearer {self.auth.token()}", "vcc-api-key": api_key, "Accept": "application/json"}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        last_error: Optional[Exception] = None
        path = url[len(API_URL):]
        for attempt in range(1, RETRIES + 1):
            try:
                response = self._session.request(method, url, headers=headers, json=body, timeout=TIMEOUT)
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                info(f"Volvo: {method} {path} attempt {attempt}/{RETRIES} failed: {e}")
        else:
            raise VolvoApiError(f"Volvo's API could not be reached: {last_error}")
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if response.status_code == 404:
            trace(f"Volvo: {method} {path} is not there for this car (404)")
            return {}
        if response.status_code == 429:
            info(f"Volvo: {method} {path} rate limited (429)")
            raise VolvoRateLimited("Volvo's API rate limit is reached (10,000 calls a day per app, about 100 a minute) — try again later.")
        if response.status_code in (401, 403):
            reason = self._error_text(data) or response.text[:200]
            info(f"Volvo: {method} {path} refused ({response.status_code}): {reason}")
            raise VolvoApiError(f"Volvo refused the call ({response.status_code}: {reason}) — the token or the app's scopes do not cover it; {sign_in_again_hint()} if it persists.")
        if response.status_code >= 400:
            reason = self._error_text(data) or response.text[:200]
            info(f"Volvo: {method} {path} failed ({response.status_code}): {reason}")
            raise VolvoApiError(f"Volvo {method} {path} failed: {response.status_code} {reason}")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error_text(data: dict) -> str:
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return " ".join(str(err.get(k)) for k in ("message", "description") if err.get(k)).strip()
        return str(err) if err else ""

    def _get_data(self, url: str) -> dict:
        body = self._request("GET", url)
        data = body.get("data", body)
        return data if isinstance(data, dict) else {}

    def _vehicle_details(self, vin: str) -> dict:
        with self._lock:
            cached = self._details.get(vin)
        if cached is not None:
            return cached
        details = self._get_data(f"{CONNECTED}/{vin}")
        if details:
            with self._lock:
                self._details[vin] = details
            trace(f"Volvo: {vin[-6:]} is a {details.get('modelYear', '')} {(details.get('descriptions') or {}).get('model', 'Volvo')}, fuel type {details.get('fuelType')}")
        else:
            info(f"Volvo: no vehicle details for {vin[-6:]} — the VIN may not be in the Volvo ID's cars")
        return details

    # --- the state -----------------------------------------------------------------------------

    @staticmethod
    def _value(fields: dict, key: str) -> Any:
        field = fields.get(key)
        return field.get("value") if isinstance(field, dict) else field

    @staticmethod
    def _number(fields: dict, key: str) -> Optional[float]:
        value = VolvoIntegration._value(fields, key)
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _timestamp(fields: dict, *keys: str) -> Optional[float]:
        """The newest `timestamp` / `updatedAt` among the named fields (all when none are named), epoch seconds."""
        newest = None
        for key in keys or fields.keys():
            field = fields.get(key)
            stamp = field.get("timestamp") or field.get("updatedAt") if isinstance(field, dict) else None
            if not stamp:
                continue
            try:
                at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            newest = at if newest is None or at > newest else newest
        return newest

    def _read(self, label: str, vin: str, fetch: Callable[[], dict]) -> dict:
        """One endpoint of the composed state; a failed read leaves its fields unknown rather
        than failing the car, except an auth or rate-limit error, which every read would repeat."""
        try:
            return fetch()
        except (NotSignedIn, VolvoRateLimited):
            raise
        except VehicleError as e:
            info(f"Volvo: {label} of {vin[-6:]} not read: {e}")
            return {}

    def vehicle_state(self, spec: VehicleSpec) -> VehicleState:
        """The car now, composed from the cached answers Volvo's servers hold (there is no live
        feed, so `data_time` is the newest timestamp among them), and kept for STATE_TTL."""
        vin = self._require_vin(spec)
        with self._lock:
            cached = self._states.get(vin)
        if cached and time.time() - cached[0] < STATE_TTL:
            trace(f"Volvo: state of {spec.label} served from the {int(time.time() - cached[0])} s old cache")
            return cached[1]
        details = self._vehicle_details(vin)
        fuel_type = str(details.get("fuelType") or "")
        has_battery = fuel_type in BATTERY_FUEL_TYPES or not fuel_type
        has_combustion = fuel_type in COMBUSTION_FUEL_TYPES

        access = self._read("command accessibility", vin, lambda: self._get_data(f"{CONNECTED}/{vin}/command-accessibility"))
        engine = self._read("engine status", vin, lambda: self._get_data(f"{CONNECTED}/{vin}/engine-status"))
        statistics = self._read("statistics", vin, lambda: self._get_data(f"{CONNECTED}/{vin}/statistics"))
        energy = self._read("energy state", vin, lambda: self._request("GET", f"{ENERGY}/{vin}/state")) if has_battery else {}
        fuel = self._read("fuel", vin, lambda: self._get_data(f"{CONNECTED}/{vin}/fuel")) if has_combustion else {}
        location = self._read("location", vin, lambda: self._get_data(f"{LOCATION}/{vin}/location"))

        availability = str(self._value(access, "availabilityStatus") or "").upper()
        engine_running = str(self._value(engine, "engineStatus") or "").upper() == "RUNNING"
        battery = self._number(energy, "batteryChargeLevel")
        if battery is None and has_battery:
            battery = self._number(fuel, "batteryChargeLevel")
        electric_range = self._number(energy, "electricRange")
        if electric_range is None:
            electric_range = self._number(statistics, "distanceToEmptyBattery")
        tank_range = self._number(statistics, "distanceToEmptyTank") if has_combustion else None
        ranges = [r for r in (electric_range, tank_range) if r is not None]

        coordinates = ((location.get("geometry") or {}).get("coordinates") or []) if isinstance(location, dict) else []
        latitude = longitude = None
        if len(coordinates) >= 2:
            longitude, latitude = coordinates[0], coordinates[1]   # GeoJSON order
        location_stamp = (location.get("properties") or {}).get("timestamp") if isinstance(location, dict) else None
        stamps = [t for t in (self._timestamp(access), self._timestamp(engine), self._timestamp(statistics),
                              self._timestamp(energy), self._timestamp(fuel),
                              self._timestamp({"location": {"timestamp": location_stamp}}) if location_stamp else None)
                  if t is not None]

        state = VehicleState(
            connectivity=CONNECTIVITY.get(availability),
            # Volvo reports no gear: an engine that runs (the drivetrain on an EV too) or a car in use is on the road
            in_gear=engine_running or availability == "CAR_IN_USE",
            speed_kmh=None,
            latitude=latitude,
            longitude=longitude,
            battery_percent=int(round(battery)) if battery is not None else None,
            fuel_percent=None,   # Volvo reports litres, not a share of the tank
            range_km=sum(ranges) if ranges else None,
            data_time=max(stamps) if stamps else None,
        )
        if not any((access, engine, statistics, energy, fuel, location)):
            raise VolvoApiError(f"Volvo answered none of the state reads for {spec.label}.")
        with self._lock:
            self._states[vin] = (time.time(), state)
        trace(f"Volvo: {spec.label} is {state.connectivity or 'of unknown reachability'}, "
              f"{'in use' if state.in_gear else 'parked'}, battery {state.battery_percent}%, range {state.range_km} km")
        return state

    # --- the commands --------------------------------------------------------------------------

    def _invoke(self, spec: VehicleSpec, command: str, body: Optional[dict] = None) -> str:
        """POSTs one of the Connected Vehicle API's commands and reads its invoke status."""
        vin = self._require_vin(spec)
        info(f"Volvo: sending {command} to {spec.label}")
        answer = self._request("POST", f"{CONNECTED}/{vin}/commands/{command}", body or {})
        data = answer.get("data") if isinstance(answer.get("data"), dict) else answer
        status = str(data.get("invokeStatus") or "").upper()
        if status not in COMMAND_OK:
            message = str(data.get("message") or "").strip()
            info(f"Volvo: {command} for {spec.label} did not complete: {status or 'no status'} {message}")
            raise VolvoCommandError(f"{spec.label} did not carry out {command}: {status or 'no answer'}" + (f" — {message}" if message else ""))
        with self._lock:
            self._states.pop(vin, None)
        trace(f"Volvo: {command} for {spec.label} {status}")
        return status

    def climate_on(self, spec: VehicleSpec) -> str:
        self._invoke(spec, "climatization-start")
        return f"Climate started in {spec.label}."

    def climate_off(self, spec: VehicleSpec) -> str:
        self._invoke(spec, "climatization-stop")
        return f"Climate stopped in {spec.label}."
