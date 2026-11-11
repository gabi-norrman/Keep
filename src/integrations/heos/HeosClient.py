"""A synchronous client of the HEOS CLI: one command in, its final JSON answer out, over TCP 1255 of any one HEOS device.

The protocol is a line of `heos://<group>/<command>?<query>` per command and a JSON object per
answer, `\\r\\n` terminated. An answer whose message says "command under process" is a
placeholder for the real one that follows; a line about another command (an event, a late
answer) is skipped. Any one device answers for the whole system — players, groups, favorites.

One connection is kept and shared behind a lock: a call that finds it broken reconnects once and
sends again. With no host configured the client finds one by SSDP (`discover()`); macOS denies
multicast to a process without the Local Network permission, which the terminal Keep starts in
has once granted."""
import json
import socket
import threading
import time
import urllib.parse
from typing import Optional

from common.logging import info, trace

PORT = 1255
SSDP_TARGET = "urn:schemas-denon-com:device:ACT-Denon:1"
TIMEOUT = 5.0
UNDER_PROCESS = "command under process"


class HeosError(Exception):
    """A HEOS command that failed or found no device; the message is what the LLM should read."""


def discover(timeout: float = 3.0) -> list[str]:
    """The addresses of the HEOS devices answering an SSDP search, in the order they answered."""
    search = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
              f"MX: 2\r\nST: {SSDP_TARGET}\r\n\r\n").encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        try:
            sock.sendto(search, ("239.255.255.250", 1900))
        except OSError as e:
            info(f"HEOS: SSDP search could not be sent ({e}) — set heos.host, or grant the terminal "
                 f"Local Network access")
            return []
        found: list[str] = []
        end = time.time() + timeout
        while time.time() < end:
            try:
                _, (address, _) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            if address not in found:
                found.append(address)
        info(f"HEOS: {len(found)} device(s) answered SSDP" + (f" ({', '.join(found)})" if found else ""))
        return found
    finally:
        sock.close()


def message_fields(answer: dict) -> dict[str, str]:
    """The `heos.message` of an answer as fields ("pid=1&state=play" → {"pid": "1", "state": "play"})."""
    return dict(urllib.parse.parse_qsl((answer.get("heos") or {}).get("message", ""), keep_blank_values=True))


class HeosClient:
    """One shared connection to a HEOS device's CLI. `host` empty: the first device SSDP finds."""

    def __init__(self, host: str = ""):
        self._configured_host = host.strip()
        self.host = self._configured_host
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self._drop()

    def _drop(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buffer = b""

    def _connect(self):
        if not self.host:
            found = discover()
            if not found:
                raise HeosError("no HEOS device answered on the network (set heos.host to one speaker's address)")
            self.host = found[0]
        try:
            self._sock = socket.create_connection((self.host, PORT), timeout=TIMEOUT)
        except OSError as e:
            failed = self.host
            if not self._configured_host:
                self.host = ""   # discovered: look again next time, the device may have a new address
            raise HeosError(f"the HEOS device at {failed} could not be reached: {e}") from e
        self._buffer = b""
        trace(f"HEOS: connected to {self.host}:{PORT}")

    def _line(self) -> dict:
        while b"\r\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("HEOS closed the connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\r\n", 1)
        return json.loads(line)

    def _exchange(self, command: str, line: bytes) -> dict:
        self._sock.sendall(line)
        while True:
            answer = self._line()
            head = answer.get("heos") or {}
            if head.get("command") != command:
                trace(f"HEOS: skipped a line about {head.get('command')!r} while waiting for {command}")
                continue
            if UNDER_PROCESS in head.get("message", ""):
                continue
            return answer

    def command(self, command: str, **params) -> dict:
        """The final answer to `heos://<command>?<params>`; raises HeosError when HEOS says it failed
        or no device answers. A broken connection is reopened once."""
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        line = f"heos://{command}{query}\r\n".encode()
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._sock is None:
                        self._connect()
                    answer = self._exchange(command, line)
                    break
                except (OSError, ConnectionError, ValueError) as e:
                    self._drop()
                    if attempt == 2:
                        raise HeosError(f"HEOS did not answer {command}: {e}") from e
                    info(f"HEOS: {command} on a broken connection ({type(e).__name__}: {e}), reconnecting once")
        head = answer.get("heos") or {}
        if head.get("result") == "fail":
            fields = message_fields(answer)
            reason = fields.get("text") or head.get("message", "")
            raise HeosError(f"HEOS refused {command}: {reason}")
        return answer
