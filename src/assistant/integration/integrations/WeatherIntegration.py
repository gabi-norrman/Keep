import datetime as dt
from typing import List
from zoneinfo import ZoneInfo

import requests

from ..Integration import Integration
from common.logging import trace, info
from common.ConfigManager import config


# noinspection PyMethodMayBeStatic
class WeatherIntegration(Integration):
    """Fetches the home location's weather forecast from the YR (met.no) API."""

    @property
    def name(self) -> str:
        return "YR Weather"

    @property
    def config_keys(self) -> List[str]:
        return ["location.home"]

    def check_health(self) -> bool:
        result = self.fetch_weather_forecast(short_term_forecast=True)
        return "Weather at" in result

    @staticmethod
    def remove_decimals(value) -> int:
        return int(round(float(value), 0))

    @staticmethod
    def fetch_weather_forecast(short_term_forecast) -> str:
        lat = config.get_home_latitude()
        lon = config.get_home_longitude()
        if lat is None or lon is None:
            return "(Weather not configured: location.home is missing)"
        base_url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
        response = requests.get(base_url, headers={'User-Agent': 'Keep_0.1'})
        if response.status_code != 200:
            raise RuntimeError("Failed to fetch weather data.")

        json = response.json()
        home_tz = ZoneInfo(config.get_home_timezone())

        # We only render the forecast at a few clock hours to keep the prompt small, which
        # leaves multi-hour gaps between rows (e.g. nothing between the late-evening row and
        # noon the next day). Rain falling inside a gap would otherwise be invisible, so we
        # sum every skipped hour's precipitation (from next_1_hours) into the next rendered
        # row. Each row therefore reports the total rain expected between it and the following
        # row, not just the rain in that single hour. "0" is always shown when we have a
        # source, so the LLM can tell a dry forecast apart from missing data.
        weather_summery = ""
        num_entries = 0
        pending_row = None        # rendered text of the most recent shown row, minus precip/symbol
        pending_symbol = None
        pending_fallback = None   # next_6_hours amount, used when no hourly data covers the window
        window_precip = 0.0       # summed next_1_hours rain from the pending row to the next shown row
        window_has_hourly = False

        def flush_pending() -> str:
            if pending_row is None:
                return ""
            block = pending_row
            if window_has_hourly:
                block += f"  precipitation_amount: {WeatherIntegration.remove_decimals(window_precip)}\n"
            elif pending_fallback is not None:
                block += f"  precipitation_amount: {WeatherIntegration.remove_decimals(pending_fallback)}\n"
            if pending_symbol is not None:
                block += f"  symbol: {pending_symbol}\n"
            return block

        for timeseries in json['properties']['timeseries']:
            # MET returns UTC timestamps; the sampling hours below are home-local by design.
            local_time = dt.datetime.fromisoformat(timeseries['time'].replace("Z", "+00:00")).astimezone(home_tz)
            local_hour = local_time.hour
            data = timeseries['data']

            hour_precip = None
            if "next_1_hours" in data:
                hour_precip = float(data['next_1_hours']['details'].get('precipitation_amount', 0.0))

            if num_entries < 8:
                detailed = True
                # more info in the first days
                shown = local_hour in (7, 10, 13, 16, 19, 22)
            else:
                detailed = False
                shown = local_hour in (12, 18)

            if not shown:
                if hour_precip is not None:
                    window_precip += hour_precip
                    window_has_hourly = True
                continue

            # Reached a row we render: the previous row's window is now complete, so flush it.
            weather_summery += flush_pending()

            # In short-term mode we only render a fixed number of rows, but we break here — at
            # the next row we would render — rather than right after rendering the last one. That
            # lets the skipped hours above finish accumulating into the final row's window first;
            # breaking earlier would truncate its rain to a single hour and let window_has_hourly
            # shadow the next_6_hours fallback. flush_pending() just emitted that final row, so
            # clear it to avoid a duplicate flush after the loop.
            if short_term_forecast and num_entries > 8:
                pending_row = None
                break

            details = data['instant']['details']
            row = f"Weather at {local_time.strftime('%Y-%m-%d %H:%M')} (local):\n"
            for key, value in details.items():
                if not (
                        key == "air_pressure_at_sea_level" or
                        key == "relative_humidity" or
                        key == "wind_from_direction" or
                        key == "precipitation_amount" or
                        (key == "cloud_area_fraction" and not detailed)):
                    row += f"  {key}: {WeatherIntegration.remove_decimals(value)}\n"

            pending_row = row
            pending_symbol = None
            pending_fallback = None
            if "next_6_hours" in data:
                pending_symbol = data['next_6_hours']['summary']['symbol_code']
                pending_fallback = float(data['next_6_hours']['details'].get('precipitation_amount', 0.0))

            # Start this row's window with its own hour.
            window_precip = hour_precip or 0.0
            window_has_hourly = hour_precip is not None

            num_entries += 1

        weather_summery += flush_pending()
        return "Temperature is in degrees Celsius, wind speed in m/s, precipitation in mm.\n" + weather_summery

    @staticmethod
    def fetch_short_term_precipitation() -> str:
        lat = config.get_home_latitude()
        lon = config.get_home_longitude()
        if lat is None or lon is None:
            return "(Precipitation not configured: location.home is missing)"
        trace("Fetching short term precipitation data from YR....")
        base_url = f"https://api.met.no/weatherapi/nowcast/2.0/complete?lat={lat}&lon={lon}"
        response = requests.get(base_url, headers={'User-Agent': 'Keep_0.1'})

        if response.status_code == 200:
            j = response.json()
            now = dt.datetime.now(dt.timezone.utc)

            radar_coverage = j.get("properties", {}).get("meta", {}).get("radar_coverage")
            if radar_coverage and radar_coverage != "ok":
                info(f"Nowcast radar_coverage is '{radar_coverage}'; short-term precipitation data not available.")
                return f"Short-term precipitation forecast unavailable (radar coverage {radar_coverage})."

            # Collect precipitation_rate (mm/h) samples in 15-minute buckets.
            # MET nowcast returns 5-min samples, so each bucket holds up to 3.
            precipitation_buckets: dict[int, list[float]] = {}

            for item in j["properties"]["timeseries"]:
                details = item["data"]["instant"]["details"]
                if "precipitation_rate" not in details:
                    continue
                amount = details["precipitation_rate"]
                time_utc = dt.datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
                delta_min = int((time_utc - now).total_seconds() // 60)

                if delta_min >= 0:
                    bucket = delta_min // 15
                    precipitation_buckets.setdefault(bucket, []).append(amount)

            if not precipitation_buckets:
                info("Nowcast returned no precipitation_rate entries; short-term precipitation data not available.")
                return "Short-term precipitation forecast unavailable."

            # Average the rates within each bucket so the value stays in mm/h.
            def fmt(bucket_idx: int) -> str:
                avg_rate = sum(precipitation_buckets[bucket_idx]) / len(precipitation_buckets[bucket_idx])
                s = str(round(avg_rate, 1))
                return "0" if s == "0.0" else s

            # Buckets 0-3 cover the next hour in 15-min slices, 4-7 cover hour 1-2.
            labels = [
                (0, "Right now (next 15 min)"),
                (1, "+15–30 min"),
                (2, "+30–45 min"),
                (3, "+45–60 min"),
            ]
            segments = [f"{label}: {fmt(idx)}" for idx, label in labels if idx in precipitation_buckets]

            hour2_buckets = [b for b in sorted(precipitation_buckets) if b >= 4]
            if hour2_buckets:
                segments.append("+1h–2h: " + ", ".join(fmt(b) for b in hour2_buckets))

            trace("Successfully fetched precipitation data.")
            return " | ".join(segments)
        else:
            raise RuntimeError("Failed to fetch precipitation data.")


Weather = WeatherIntegration
