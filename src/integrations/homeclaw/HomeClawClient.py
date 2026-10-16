"""The HomeClaw app's local Unix socket, one request per connection.

A request is one JSON line, `{"command": ..., "args": {...}}`; the app answers with one JSON
line, `{"success": true, "data": ...}` or `{"success": false, "error": "..."}`, and closes.
`call` returns the data and raises HomeClawError with the app's message otherwise. The app
not running — no socket file, or a socket nobody accepts on — is HomeClawUnavailable, so the
callers can say that in plain words instead of an errno."""
import json
import os
import socket
from pathlib import Path
from typing import Any

from common.logging import trace

DEFAULT_SOCKET = "~/Library/Group Containers/group.com.shahine.homeclaw/homeclaw.sock"
TIMEOUT = 15            # seconds per request; a control with verify waits for the accessory to answer
BUFFER = 65536


class HomeClawError(Exception):
    """What the app answered with success false, or a reply that is not the envelope."""


class HomeClawUnavailable(HomeClawError):
    """The app is not running: no socket file, or nothing accepting on it."""


class HomeClawClient:
    """Talks to the app over its socket; nothing is kept between requests."""

    def __init__(self, socket_path: str = "", timeout: float = TIMEOUT):
        self.path = Path(os.path.expanduser(socket_path or DEFAULT_SOCKET))
        self.timeout = timeout

    def call(self, command: str, **args: Any) -> Any:
        """The `data` of the app's reply to `command`, sent with the args that are not None."""
        request = {"command": command, "args": {k: v for k, v in args.items() if v is not None}}
        if not self.path.exists():
            raise HomeClawUnavailable(f"HomeClaw is not running (no socket at {self.path})")
        try:
            raw = self._exchange(json.dumps(request))
        except (ConnectionRefusedError, FileNotFoundError, BrokenPipeError, ConnectionResetError):
            raise HomeClawUnavailable("HomeClaw is not running (its socket accepts no connection)")
        except socket.timeout:
            raise HomeClawError(f"HomeClaw did not answer {command} within {self.timeout:g} s")
        try:
            reply = json.loads(raw)
        except ValueError:
            raise HomeClawError(f"HomeClaw answered {command} with something that is not JSON")
        if not isinstance(reply, dict):
            raise HomeClawError(f"HomeClaw answered {command} with {type(reply).__name__}, not an envelope")
        if not reply.get("success"):
            raise HomeClawError(str(reply.get("error") or f"{command} failed"))
        trace(f"HomeClaw: {command} answered ({len(raw)} bytes)")
        return reply.get("data")

    def _exchange(self, line: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(self.path))
            sock.sendall((line + "\n").encode("utf-8"))
            chunks = []
            while True:
                chunk = sock.recv(BUFFER)
                if not chunk:
                    break
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
        return b"".join(chunks).decode("utf-8").strip()
