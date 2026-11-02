import asyncio

import pytest
from quart import Quart

from interfaces.http import CrossSiteGuard

HOST = "keep.local:8888"


@pytest.fixture
def app():
    a = Quart(__name__)
    CrossSiteGuard.install(a)

    @a.route('/stream', methods=['GET', 'POST'])
    async def stream():
        return "ran", 200

    return a


def call(app, method, headers):
    async def run():
        client = app.test_client()
        r = await client.open("/stream?query=hi", method=method, headers={"Host": HOST, **headers})
        return r.status_code, await r.get_data()
    return asyncio.run(run())


@pytest.mark.parametrize("headers", [
    {},  # the iOS and Android apps: URLSession / HttpURLConnection send neither header
    {"User-Agent": "Floxy%20AI/1 CFNetwork/1490 Darwin/24.0.0"},
    {"Sec-Fetch-Site": "none"},  # the owner typed the URL into the address bar
    {"Sec-Fetch-Site": "same-origin", "Origin": f"http://{HOST}"},
])
def test_native_clients_and_the_servers_own_pages_pass(app, headers):
    assert call(app, "GET", headers) == (200, b"ran")


@pytest.mark.parametrize("method,headers", [
    ("GET", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "image"}),  # <img src=…> on a web page
    ("GET", {"Sec-Fetch-Site": "same-site"}),
    ("POST", {"Origin": "https://evil.example"}),  # a form or fetch() from a browser too old for Sec-Fetch-*
    ("POST", {"Origin": "http://keep.local:9999"}),  # another port on the same host is another origin
])
def test_a_request_a_browser_sends_for_another_site_is_refused_before_the_route(app, method, headers):
    status, body = call(app, method, headers)
    assert status == 403 and body != b"ran"
