"""The CURRENT CAR INFO section: one line per car of every ready VehicleProvider — Tesla
beside whatever other brand the house has — with the car's vehicle_id, whether it is parked or
driving, where it is (Home, or an address), its route or how far from home it is, the cabin
temperature and the charge. Refreshed every five minutes while every car is parked and every
minute while one is driving; a brand that fails gets an error on its car's line and the others
stay. Without a ready provider the section is the setup hint."""
import math
from datetime import datetime
from typing import Optional

import requests

from assistant.integration.VehicleProvider import VehicleProvider, VehicleSpec, VehicleState, active, Fleet, no_provider_hint
from assistant.tools.Geocoding import reverse_geocode
from common.ConfigManager import config
from common.logging import info, trace

from ..PromptSectionGenerator import PromptSectionGenerator


class CarSectionGenerator(PromptSectionGenerator):
    """Refreshes the car section from every ready car integration; runs only while one is on."""
    key = "car_info"
    heading = "# CURRENT CAR INFO"
    intro = ("The family's cars, every brand together. `vehicle_id` is what the `cars` toolkit's commands take. "
             "A car marked asleep or offline shows the brand's last cached data.")
    needs = VehicleProvider

    HOME_RADIUS_METERS = 500
    UPDATE_INTERVAL_DRIVING = 60
    UPDATE_INTERVAL_PARKED = 300
    UPDATE_INTERVAL_NOT_READY = 30      # a provider is on but not configured yet: look again soon after setup

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "CarInfo")
        # Optional: without a home location the cars are placed by address only.
        self.home_lat = config.get_home_latitude()
        self.home_lon = config.get_home_longitude()
        self._any_car_driving = False
        self._ready = False

    def _start_monitoring(self):
        while not self._stop_event.is_set():
            self._update_content()
            self._wait(self._get_update_interval())

    def _get_update_interval(self) -> int:
        """Driving status is carried over from the last fetch pass — fresh state calls here
        would double the traffic to the brands' clouds for nothing."""
        if not self._ready:
            return self.UPDATE_INTERVAL_NOT_READY
        return self.UPDATE_INTERVAL_DRIVING if self._any_car_driving else self.UPDATE_INTERVAL_PARKED

    def _fetch_content(self) -> str:
        providers = active()
        self._ready = bool(providers)
        if not providers:
            self._any_car_driving = False
            return f"({no_provider_hint()})"

        trace(f"Fetching car information from {', '.join(p.name for p in providers)}...")
        self._any_car_driving = False
        lines = []
        for provider, spec in Fleet(providers).cars():
            try:
                lines.append(self._render(spec, provider.vehicle_state(spec)))
            except Exception as e:
                info(f"CarInfo: {provider.name} failed for {spec.label}: {e}")
                lines.append(f"{spec.label} (vehicle_id: {spec.id}): Error fetching data - {e}")
        if not lines:
            info("CarInfo: the providers list no cars")
            return "No car information available."
        trace(f"{len(lines)} lines of car information fetched.")
        return "\n".join(lines)

    def _render(self, spec: VehicleSpec, state: VehicleState) -> str:
        """One car's line for the prompt."""
        is_driving = state.is_driving
        if is_driving or state.in_gear:
            self._any_car_driving = True
        status = "Driving" if is_driving else "Parked"
        speed_text = f"{state.speed_kmh} km/h" if is_driving else ""

        has_position = state.latitude is not None and state.longitude is not None
        if has_position and self.home_lat is not None and self.home_lon is not None:
            distance_m = self._haversine_distance(self.home_lat, self.home_lon, state.latitude, state.longitude)
            if distance_m <= self.HOME_RADIUS_METERS:
                location_text = "Home"
            else:
                location_text = reverse_geocode(state.latitude, state.longitude) or "Not home"
        else:
            location_text = "Location unknown"
        is_at_home = location_text == "Home"

        # A route left loaded in the nav of a parked car would contradict "Parked" (Tesla keeps
        # the route until it is cancelled), so only a car in gear is navigating.
        has_route = state.minutes_to_arrival is not None and state.destination and (is_driving or state.in_gear)
        if has_route:
            eta_text = f"Navigating to {state.destination}, ETA {round(state.minutes_to_arrival)} min"
        elif not is_at_home and has_position:
            eta_minutes = self._get_eta_to_home(state.latitude, state.longitude)
            eta_text = f"~{eta_minutes} min from home" if eta_minutes is not None else ""
        else:
            eta_text = ""

        cabin_text = f"Cabin {state.cabin_temp_c}°C" if state.cabin_temp_c is not None else ""
        if state.battery_percent is not None:
            energy_text = f"({state.battery_percent}% battery)"
        elif state.fuel_percent is not None:
            energy_text = f"({state.fuel_percent}% fuel)"
        else:
            energy_text = "Battery unknown"
        range_text = f"{round(state.range_km)} km range" if state.range_km is not None else ""

        # The brand serves a cached snapshot while the car sleeps — flag it so the LLM knows
        # the data may be stale.
        asleep_text = ""
        if state.connectivity is not None and state.connectivity != "online":
            if state.data_time:
                asleep_text = f"[car is {state.connectivity}, data from {datetime.fromtimestamp(state.data_time).strftime('%H:%M')}]"
            else:
                asleep_text = f"[car is {state.connectivity}, data may be stale]"

        parts = [f"{spec.label} (vehicle_id: {spec.id}):", status, speed_text, location_text, eta_text,
                 cabin_text, energy_text, range_text, asleep_text]
        return " ".join(p for p in parts if p)

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """The great circle distance in meters between two points given in decimal degrees."""
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * math.asin(math.sqrt(a)) * 6371000

    def _get_eta_to_home(self, latitude: float, longitude: float) -> Optional[int]:
        """ETA to home in minutes from the Google Distance Matrix API with traffic; None on
        error or without an API key."""
        try:
            api_key = config.get('api_keys.google_maps', "")
            if not api_key or self.home_lat is None or self.home_lon is None:
                return None

            url = "https://maps.googleapis.com/maps/api/distancematrix/json"
            params = {
                "origins": f"{latitude},{longitude}",
                "destinations": f"{self.home_lat},{self.home_lon}",
                "mode": "driving",
                "departure_time": "now",
                "key": api_key,
            }

            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "OK":
                    elements = data.get("rows", [{}])[0].get("elements", [{}])[0]
                    if elements.get("status") == "OK":
                        duration = elements.get("duration_in_traffic") or elements.get("duration")
                        if duration:
                            trace("Successfully fetched ETA to home.")
                            return round(duration.get("value", 0) / 60)
            return None
        except Exception:
            return None
