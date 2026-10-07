"""A stand-in for `requests.request` answering the shapes Graph v1.0 sends, and a stub auth
that hands the client a token; the fixtures are written from the Graph docs' examples with
the fictional cast."""
import json as jsonlib
from dataclasses import dataclass, field
from typing import Optional

from integrations.microsoft.GraphClient import BASE_URL
from integrations.microsoft.MicrosoftAuth import NotSignedIn, not_signed_in_hint


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


@dataclass
class FakeGraph:
    """`route(method, path, body)` answers a request; a callable route gets (params, headers, json)
    and returns a FakeResponse. Every request is kept in `calls`."""
    routes: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def route(self, method: str, path: str, body=None, status: int = 200, headers: Optional[dict] = None):
        self.routes[(method, path)] = body if callable(body) else FakeResponse(status, body, headers)

    def __call__(self, method, url, params=None, headers=None, json=None, timeout=None):
        path = url[len(BASE_URL):] if url.startswith(BASE_URL) else url
        self.calls.append(Call(method, path, dict(params or {}), dict(headers or {}), json))
        handler = self.routes.get((method, path))
        if handler is None:
            return FakeResponse(404, {"error": {"code": "ErrorItemNotFound", "message": f"nothing at {method} {path}"}})
        return handler(params, headers, json) if callable(handler) else handler

    def sent(self, method: str, path: str) -> list:
        return [c for c in self.calls if c.method == method and c.path == path]


class StubAuth:
    def __init__(self, name="Work", token="tok-1", signed_in=True):
        self.name = name
        self._token = token
        self._signed_in = signed_in

    def signed_in(self) -> bool:
        return self._signed_in

    def token(self) -> str:
        if not self._signed_in:
            raise NotSignedIn(not_signed_in_hint(self.name))
        return self._token


def recipient(address: str, name: str = "") -> dict:
    return {"emailAddress": {"address": address, **({"name": name} if name else {})}}


def message(uid: str, subject: str, sender: tuple, received: str, read: bool, body: Optional[tuple] = None, **more) -> dict:
    item = {"id": uid, "subject": subject, "from": recipient(*sender), "receivedDateTime": received, "isRead": read, **more}
    if body is not None:
        item["body"] = {"contentType": body[0], "content": body[1]}
    return item
