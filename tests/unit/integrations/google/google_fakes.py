"""Stand-ins for `requests` answering the shapes Google's token endpoint and the Calendar API v3
send, and a stub auth that hands the calendar account a token; the fixtures are written from
the Google docs' examples with the fictional cast."""
import json as jsonlib
import time
from dataclasses import dataclass, field
from typing import Optional

from integrations.google.GoogleAuth import TOKEN_URL, NotSignedIn, not_signed_in_hint
from integrations.google.GoogleCalendarAccount import BASE_URL


class FakeResponse:
    def __init__(self, status: int, body=None, headers: Optional[dict] = None):
        self.status_code = status
        self.headers = headers or {}
        self.content = jsonlib.dumps(body).encode() if body is not None else b""
        self.text = self.content.decode()

    def json(self):
        return jsonlib.loads(self.content)


@dataclass
class Call:
    method: str
    path: str
    params: dict
    headers: dict
    json: Optional[dict]
    data: Optional[dict] = None


@dataclass
class FakeGoogle:
    """`route(method, path, body)` answers a Calendar request (path relative to the API base);
    `token_answers` is what the token endpoint answers next, in order. A callable route gets
    (params, json) and returns a FakeResponse. Every request is kept in `calls`."""
    routes: dict = field(default_factory=dict)
    token_answers: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def route(self, method: str, path: str, body=None, status: int = 200):
        self.routes[(method, path)] = body if callable(body) else FakeResponse(status, body)

    def answer_token(self, body: dict, status: int = 200):
        self.token_answers.append(FakeResponse(status, body))

    # requests.request, as the calendar account calls it
    def __call__(self, method, url, params=None, headers=None, json=None, timeout=None):
        path = url[len(BASE_URL):] if url.startswith(BASE_URL) else url
        self.calls.append(Call(method, path, dict(params or {}), dict(headers or {}), json))
        handler = self.routes.get((method, path))
        if handler is None:
            return FakeResponse(404, {"error": {"code": 404, "message": f"nothing at {method} {path}", "errors": [{"reason": "notFound"}]}})
        return handler(params, json) if callable(handler) else handler

    # requests.post, as the auth calls it for the token endpoint
    def post(self, url, data=None, timeout=None):
        self.calls.append(Call("POST", url, {}, {}, None, dict(data or {})))
        assert url == TOKEN_URL, f"posted to {url}"
        if not self.token_answers:
            return FakeResponse(400, {"error": "invalid_request", "error_description": "no answer scripted"})
        return self.token_answers.pop(0)

    def sent(self, method: str, path: str) -> list:
        return [c for c in self.calls if c.method == method and c.path == path]

    def token_posts(self) -> list:
        return [c for c in self.calls if c.path == TOKEN_URL]


def token_grant(access_token="ya29.access", refresh_token="1//refresh", expires_in=3599,
                scope="https://mail.google.com/ https://www.googleapis.com/auth/calendar") -> dict:
    body = {"access_token": access_token, "expires_in": expires_in, "scope": scope, "token_type": "Bearer"}
    if refresh_token:
        body["refresh_token"] = refresh_token
    return body


def token_file(address="marcus@example.com", expires_in=3000, refresh_token="1//refresh", access_token="ya29.old") -> dict:
    return {"refresh_token": refresh_token, "access_token": access_token, "expiry": time.time() + expires_in,
            "scope": "https://mail.google.com/ https://www.googleapis.com/auth/calendar", "address": address}


class StubAuth:
    def __init__(self, name="Personal", token="ya29.tok", signed_in=True):
        self.name = name
        self._token = token
        self._signed_in = signed_in
        self.refresh_error = ""

    def signed_in(self) -> bool:
        return self._signed_in

    def needs_sign_in_again(self) -> bool:
        return False

    def token(self) -> str:
        if not self._signed_in:
            raise NotSignedIn(not_signed_in_hint(self.name))
        return self._token


def calendar_list(*items) -> dict:
    return {"items": list(items)}


def calendar(summary: str, calendar_id: str, primary: bool = False, **more) -> dict:
    item = {"id": calendar_id, "summary": summary, "accessRole": "owner", **more}
    if primary:
        item["primary"] = True
    return item


def timed(summary: str, start: str, end: str, **more) -> dict:
    return {"summary": summary, "status": "confirmed", "start": {"dateTime": start}, "end": {"dateTime": end}, **more}


def all_day(summary: str, start: str, end: str, **more) -> dict:
    return {"summary": summary, "status": "confirmed", "start": {"date": start}, "end": {"date": end}, **more}


def events_page(*items, next_page_token: Optional[str] = None) -> dict:
    page = {"items": list(items), "timeZone": "Europe/Stockholm"}
    if next_page_token:
        page["nextPageToken"] = next_page_token
    return page
