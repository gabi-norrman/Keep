"""Sunrise and sunset of a day at a place, the way the Almanac for Computers (1990) computes
them: good to a few minutes, which is what a HomeKit automation that fires "at sunset-45m"
needs to be verified at the right moment. No library — the tree has none for it."""
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ZENITH = 90.833     # the official sunrise and sunset: the upper limb of the disc, refracted


def sun_times(day: date, latitude: float, longitude: float, tz: str) -> Optional[tuple[datetime, datetime]]:
    """Sunrise and sunset of `day` (a local date) as aware local datetimes, or None when the
    sun neither rises nor sets that day (a polar night or day)."""
    zone = ZoneInfo(tz)
    rise = _event(day, latitude, longitude, rising=True)
    down = _event(day, latitude, longitude, rising=False)
    if rise is None or down is None:
        return None
    return rise.astimezone(zone), down.astimezone(zone)


def _event(day: date, latitude: float, longitude: float, rising: bool) -> Optional[datetime]:
    day_of_year = day.timetuple().tm_yday
    longitude_hour = longitude / 15
    t = day_of_year + ((6 if rising else 18) - longitude_hour) / 24
    mean_anomaly = 0.9856 * t - 3.289
    true_longitude = (mean_anomaly + 1.916 * _sin(mean_anomaly) + 0.020 * _sin(2 * mean_anomaly) + 282.634) % 360
    right_ascension = math.degrees(math.atan(0.91764 * math.tan(math.radians(true_longitude)))) % 360
    right_ascension += (math.floor(true_longitude / 90) - math.floor(right_ascension / 90)) * 90
    right_ascension /= 15
    sin_declination = 0.39782 * _sin(true_longitude)
    cos_declination = math.cos(math.asin(sin_declination))
    cos_hour = ((_cos(ZENITH) - sin_declination * _sin(latitude)) / (cos_declination * _cos(latitude)))
    if cos_hour > 1 or cos_hour < -1:
        return None
    hour_angle = math.degrees(math.acos(cos_hour))
    hour_angle = (360 - hour_angle if rising else hour_angle) / 15
    local_mean = hour_angle + right_ascension - 0.06571 * t - 6.622
    universal = (local_mean - longitude_hour) % 24
    midnight = datetime.combine(day, time(0), tzinfo=timezone.utc)
    return midnight + timedelta(hours=universal)


def _sin(degrees: float) -> float:
    return math.sin(math.radians(degrees))


def _cos(degrees: float) -> float:
    return math.cos(math.radians(degrees))
