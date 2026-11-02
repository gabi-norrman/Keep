"""Refuses HTTP requests a web page in a browser on the LAN sends on another site's behalf.

The server has no authentication, so a page the owner opens could otherwise make the
browser fire `<img src="http://<mac>:8888/stream?query=…">` at it. Browsers mark such
requests (`Sec-Fetch-Site`, `Origin`); the native Outposts (iOS URLSession, Android,
Python clients) send neither header, so they pass untouched."""
from typing import Optional
from urllib.parse import urlsplit

from common.logging import info, trace

# `none` is a URL the user typed or bookmarked themselves.
ALLOWED_FETCH_SITES = ('same-origin', 'none')


def rejection(headers, host: str) -> Optional[str]:
    """Why a request with these headers, addressed to `host`, comes from another site — None when it does not."""
    fetch_site = headers.get('Sec-Fetch-Site')
    if fetch_site is not None and fetch_site not in ALLOWED_FETCH_SITES:
        return f"Sec-Fetch-Site: {fetch_site}"
    origin = headers.get('Origin')
    if origin is not None and urlsplit(origin).netloc != host:
        return f"Origin {origin} is not {host}"
    return None


def install(app) -> None:
    from quart import request

    @app.before_request
    async def refuse_cross_site():
        reason = rejection(request.headers, request.host)
        if reason is None:
            return None
        info(f"Refused cross-site {request.method} {request.path} from {request.remote_addr}: {reason}")
        return {"error": "cross-site request refused"}, 403

    trace("Cross-site guard installed on the HTTP app.")
