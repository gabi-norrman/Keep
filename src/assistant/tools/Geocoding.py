from typing import Optional

import requests

from common.logging import trace


# Home city — when the resolved address is in this city, omit the city portion
# from the concise label since the user already knows we live there. Compared
# case-insensitively. Encoded as escapes to avoid mojibake when the file is
# read on systems with surprising default encodings.
_HOME_CITY_LOWER = "göteborg"


def reverse_geocode(latitude: float, longitude: float, timeout: int = 5) -> Optional[str]:
    """Resolve lat/lon to a concise human-readable address using Nominatim.

    Returns a string like "Vasagatan, Vasastaden" (road, neighbourhood, and
    city — city dropped when it's the home city), or the full display_name
    when no individual fields are present, or None on error.
    """
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": latitude,
            "lon": longitude,
            "format": "json",
            "addressdetails": 1,
        }
        headers = {"User-Agent": "Keep-AI-Assistant/1.0"}

        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code != 200:
            trace(f"reverse_geocode: HTTP {response.status_code}")
            return None

        data = response.json()
        address = data.get("address", {})

        parts = []
        if "road" in address:
            parts.append(address["road"])
        if "suburb" in address or "neighbourhood" in address:
            parts.append(address.get("suburb") or address.get("neighbourhood"))
        if "city" in address or "town" in address or "village" in address:
            city = address.get("city") or address.get("town") or address.get("village")
            if city and city.lower() != _HOME_CITY_LOWER:
                parts.append(city)

        return ", ".join(parts) if parts else data.get("display_name", "Unknown location")
    except Exception as e:
        trace(f"reverse_geocode failed for ({latitude}, {longitude}): {e}")
        return None
