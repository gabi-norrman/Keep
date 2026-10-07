import math
import time
import requests
from datetime import datetime
from typing import Optional

from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import info, trace
from integrations.tesla.TeslaIntegration import TeslaIntegration
from common.ConfigManager import config
from assistant.tools.Geocoding import reverse_geocode


class TeslaSectionGenerator(PromptSectionGenerator):
    """Refreshes the Tesla vehicle section every minute while driving, every 5 minutes when parked.

    Reads the integration the IntegrationMonitor built (it runs only while Tesla is on); while
    that is not configured the section says so and looks again at the parked cadence."""
    key = "car_info"
    heading = "# CURRENT TESLA CAR INFO"

    HOME_RADIUS_METERS = 500
    UPDATE_INTERVAL_DRIVING = 60  # 1 minute when driving
    UPDATE_INTERVAL_PARKED = 300  # 5 minutes when parked

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "TeslaInfo")
        self.tesla_api = None
        # Optional: without a home location the cars are placed by address only.
        self.home_lat = config.get_home_latitude()
        self.home_lon = config.get_home_longitude()
        self._any_car_driving = False

    def _start_monitoring(self):
        """Update every 1 minute when driving, 5 minutes when parked."""
        while not self._stop_event.is_set():
            api = TeslaIntegration.peek_instance()
            if api is not self.tesla_api:
                info(f"TeslaInfo: the Tesla integration is {'running' if api else 'not configured'}")
                self.tesla_api = api
            self._update_content()
            self._wait(self._get_update_interval())

    def _get_update_interval(self) -> int:
        """
        Determine update interval based on whether any car is driving.
        Driving status is carried over from the last fetch pass - making fresh
        /state calls here would double the Tessie traffic for nothing.
        """
        if self._any_car_driving:
            return self.UPDATE_INTERVAL_DRIVING
        return self.UPDATE_INTERVAL_PARKED

    def _fetch_content(self) -> str:
        """Fetch Tesla vehicle information for all cars."""
        if not self.tesla_api:
            return "(Tesla is on but not configured yet — the setup toolkit configures it)"

        trace("Fetching Tesla vehicle information...")

        vehicle_specs = self.tesla_api._vehicle_specs
        lines = []
        self._any_car_driving = False

        for spec in vehicle_specs:
            try:
                car_info = self._get_vehicle_info(spec.vin, spec.label(), spec.name)
                lines.append(car_info)
            except Exception as e:
                lines.append(f"{spec.label()}: Error fetching data - {str(e)}")

        if not lines:
            return "No Tesla vehicle information available."

        trace(f"{len(lines)} lines of vehicle information fetched.")

        return "\n".join(lines)

    def _get_vehicle_info(self, vin: Optional[str], label: str, name: str) -> str:
        """Get formatted info string for a single vehicle."""
        if not vin:
            return f"{label}: VIN not configured"

        state = self.tesla_api._api_get(vin, "state")
        vehicle_state = state.get("state")  # "online" / "asleep" / "offline"
        drive_state = state.get("drive_state", {})
        charge_state = state.get("charge_state", {})
        climate_state = state.get("climate_state", {})

        # Extract data
        latitude = drive_state.get("latitude")
        longitude = drive_state.get("longitude")
        speed = drive_state.get("speed")
        shift_state = drive_state.get("shift_state")
        battery_level = charge_state.get("usable_battery_level")
        minutes_to_arrival = drive_state.get("active_route_minutes_to_arrival")
        destination = drive_state.get("active_route_destination")

        # Determine driving status
        is_driving = speed is not None and speed > 0
        # In gear counts as driving for navigation purposes (e.g. stopped at a red light)
        in_gear = shift_state in ("D", "R", "N")
        status = "Driving" if is_driving else "Parked"
        if is_driving or in_gear:
            self._any_car_driving = True

        # Speed text
        speed_text = f"{speed} km/h" if is_driving else ""

        # Home/Not home
        if latitude is not None and longitude is not None and self.home_lat is not None and self.home_lon is not None:
            distance_m = self._haversine_distance(
                self.home_lat, self.home_lon, latitude, longitude
            )
            if distance_m <= self.HOME_RADIUS_METERS:
                location_text = "Home"
            else:
                # Look up address when not at home
                address = reverse_geocode(latitude, longitude)
                location_text = address if address else "Not home"
        else:
            location_text = "Location unknown"

        # ETA text
        is_at_home = location_text == "Home"

        has_route = minutes_to_arrival is not None and destination
        if has_route and not (is_driving or in_gear):
            # A route is loaded in the nav but the car is parked - showing "Navigating to X"
            # would contradict "Parked" (Tesla keeps the route loaded until cancelled)
            has_route = False

        if has_route:
            # Tesla is actively navigating somewhere
            eta_text = f"Navigating to {destination}, ETA {round(minutes_to_arrival)} min"
        elif not is_at_home and latitude is not None and longitude is not None:
            # Not at home and not navigating - look up ETA to home via Google (informational only)
            eta_minutes = self._get_eta_to_home(latitude, longitude)
            if eta_minutes is not None:
                eta_text = f"~{eta_minutes} min from home"
            else:
                eta_text = ""
        else:
            eta_text = ""

        # Cabin temperature
        inside_temp = climate_state.get("inside_temp")
        cabin_text = f"Cabin {inside_temp}°C" if inside_temp is not None else ""

        # Battery text
        battery_text = f"({battery_level}% battery)" if battery_level is not None else "Battery unknown"

        # Tessie serves a cached snapshot while the car sleeps - flag it so the LLM
        # knows the data may be stale
        asleep_text = ""
        if vehicle_state is not None and vehicle_state != "online":
            timestamp_ms = drive_state.get("timestamp")
            if timestamp_ms:
                data_time = datetime.fromtimestamp(timestamp_ms / 1000).strftime("%H:%M")
                asleep_text = f"[car is {vehicle_state}, data from {data_time}]"
            else:
                asleep_text = f"[car is {vehicle_state}, data may be stale]"

        # Build the info line
        parts = [label + " (vehicle_id: " + name + "):", status, speed_text, location_text, eta_text, cabin_text, battery_text, asleep_text]
        # Filter out empty strings
        parts = [p for p in parts if p]

        return " ".join(parts)

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the great circle distance in meters between two points
        on the earth (specified in decimal degrees).
        """
        # Convert decimal degrees to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))

        # Radius of earth in meters
        r = 6371000
        return c * r

    def _get_eta_to_home(self, latitude: float, longitude: float) -> Optional[int]:
        """
        Get ETA to home in minutes using Google Distance Matrix API with traffic.
        Returns None on error or if API key not configured.
        """
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
                        # Prefer duration_in_traffic, fall back to duration
                        duration = elements.get("duration_in_traffic") or elements.get("duration")
                        if duration:
                            seconds = duration.get("value", 0)
                            trace("Successfully fetched ETA to home.")
                            return round(seconds / 60)
            return None
        except Exception:
            return None
