"""Stand-ins for `requests` answering the shapes Volvo ID's token endpoint and the Volvo Cars
APIs send, written from the API reference's examples with a fictional car (VIN YV1KEP47L00000001)."""
import json as jsonlib
import time
from dataclasses import dataclass, field
from typing import Optional

from integrations.volvo.VolvoAuth import TOKEN_URL
from integrations.volvo.VolvoIntegration import API_URL

VIN = "YV1KEP47L00000001"
NOW_ISO = "2026-10-09T08:15:00Z"


class FakeResponse:
    def __init__(self, status: int, body=None):
        self.status_code = status
        self.content = jsonlib.dumps(body).encode() if body is not None else b""
        self.text = self.content.decode()

    def json(self):
        return jsonlib.loads(self.content)


@dataclass
class Call:
    method: str
    path: str
    headers: dict
    json: Optional[dict]
    data: Optional[dict] = None


@dataclass
class FakeVolvo:
    """`route(method, path, body)` answers an API request (path relative to api.volvocars.com);
    `token_answers` is what the token endpoint answers next, in order. Every request is kept
    in `calls`; a routeless request answers 404 as the API does for an endpoint the car lacks."""
    routes: dict = field(default_factory=dict)
    token_answers: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    transport_errors: list = field(default_factory=list)

    def route(self, method: str, path: str, body=None, status: int = 200):
        self.routes[(method, path)] = FakeResponse(status, body)

    def answer_token(self, body: dict, status: int = 200):
        self.token_answers.append(FakeResponse(status, body))

    # requests.Session.request, as the integration calls it
    def request(self, method, url, headers=None, json=None, timeout=None):
        if self.transport_errors:
            raise self.transport_errors.pop(0)
        path = url[len(API_URL):] if url.startswith(API_URL) else url
        self.calls.append(Call(method, path, dict(headers or {}), json))
        answer = self.routes.get((method, path))
        return answer if answer is not None else FakeResponse(404, {"error": {"message": "NOT_FOUND", "description": f"nothing at {method} {path}"}})

    # requests.post, as the auth calls it for the token endpoint
    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(Call("POST", url, dict(headers or {}), None, dict(data or {})))
        assert url == TOKEN_URL, f"posted to {url}"
        if not self.token_answers:
            return FakeResponse(400, {"error": "invalid_request", "error_description": "no answer scripted"})
        return self.token_answers.pop(0)

    def sent(self, method: str, path: str) -> list:
        return [c for c in self.calls if c.method == method and c.path == path]

    def api_calls(self) -> list:
        return [c for c in self.calls if c.path != TOKEN_URL]


def token_grant(access_token="at.new", refresh_token="rt.new", expires_in=1799) -> dict:
    body = {"access_token": access_token, "token_type": "Bearer", "expires_in": expires_in, "id_token": "id.x"}
    if refresh_token:
        body["refresh_token"] = refresh_token
    return body


def token_file(expires_in=1500, refresh_token="rt.old", access_token="at.old") -> dict:
    return {"refresh_token": refresh_token, "access_token": access_token, "expiry": time.time() + expires_in}


def value(v, stamp=NOW_ISO, unit=None) -> dict:
    field_ = {"value": v, "timestamp": stamp}
    if unit:
        field_["unit"] = unit
    return field_


def energy_value(v, stamp=NOW_ISO, unit=None) -> dict:
    field_ = {"value": v, "status": "OK", "updatedAt": stamp}
    if unit:
        field_["unit"] = unit
    return field_


def details(fuel_type="ELECTRIC", model="XC40") -> dict:
    return {"data": {"vin": VIN, "modelYear": 2024, "gearbox": "AUTOMATIC", "fuelType": fuel_type,
                     "descriptions": {"model": model, "steering": "LEFT"}, "externalColour": "Crystal White"}}


def location(longitude=17.9876, latitude=59.3456, heading="275", stamp=NOW_ISO) -> dict:
    return {"data": {"type": "Feature", "geometry": {"type": "Point", "coordinates": [longitude, latitude, 12.0]},
                     "properties": {"heading": heading, "timestamp": stamp}}}


def command_result(status="COMPLETED", message="") -> dict:
    return {"data": {"vin": VIN, "invokeStatus": status, "message": message}}


class StubAuth:
    def __init__(self, token="at.stub", signed_in=True, again=False):
        self._token = token
        self._signed_in = signed_in
        self._again = again
        self.refresh_error = "the Volvo sign-in lapsed" if again else ""

    def signed_in(self) -> bool:
        return self._signed_in

    def needs_sign_in_again(self) -> bool:
        return self._again

    def token(self) -> str:
        from integrations.volvo.VolvoAuth import NotSignedIn, not_signed_in_hint
        if not self._signed_in:
            raise NotSignedIn(not_signed_in_hint())
        return self._token

    def expires_at(self):
        return None
