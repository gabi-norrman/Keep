"""The HEOS change events: a connection of its own registered for them, read on a thread, each event handed to a callback.

`system/register_for_change_events?enable=on` makes HEOS push a line per change on that
connection — `event/player_state_changed`, `event/player_now_playing_changed`,
`event/players_changed`, `event/groups_changed` and others, each with its fields in `message`. The
events never share the command connection, whose answers they would interleave with.

The connection is idle between changes, so a heart beat goes out after `IDLE_SECONDS` of silence
and an unanswered one counts as a dead connection. A dead or refused connection is retried after
`RETRY_MIN` seconds, doubling to `RETRY_MAX`, and from `RETRY_MIN` again once a stream has
listened; `connected` is true only while events can arrive, so
a caller relying on them falls back to asking HEOS itself in between."""
import json
import socket
import threading
from typing import Callable, Optional

from common.logging import error, info, trace
from integrations.heos.HeosClient import PORT, message_fields

IDLE_SECONDS = 30
RETRY_MIN = 5
RETRY_MAX = 60
CONNECT_TIMEOUT = 5


class HeosEvents:
    """The event thread. `host()` names the device to listen to ("" while none is known);
    `on_event(name, fields)` runs on the thread for every event, `on_connected(bool)` when the
    stream comes or goes."""

    def __init__(self, host: Callable[[], str], on_event: Callable[[str, dict], None],
                 on_connected: Callable[[bool], None] = lambda up: None):
        self._host = host
        self._on_event = on_event
        self._on_connected = on_connected
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self.connected = False

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="HeosEvents", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _set_connected(self, up: bool):
        if up != self.connected:
            self.connected = up
            info(f"HEOS events: {'listening' if up else 'not listening'}")
            try:
                self._on_connected(up)
            except Exception as e:
                error(f"HEOS events: the connected callback failed: {e}")

    def _run(self):
        delay = RETRY_MIN
        while not self._stop.is_set():
            host = self._host()
            if not host:
                trace(f"HEOS events: no device known yet, looking again in {delay} s")
            else:
                try:
                    self._listen(host)
                except (OSError, ConnectionError, ValueError) as e:
                    if self._stop.is_set():
                        break
                    if self.connected:
                        delay = RETRY_MIN   # the stream listened: its failure starts the backoff over
                    info(f"HEOS events: the stream from {host} broke ({type(e).__name__}: {e}), retrying in {delay} s")
                finally:
                    self._set_connected(False)
                    self._sock = None
            if self._stop.wait(delay):
                break
            delay = min(RETRY_MAX, delay * 2)
        trace("HEOS events: stopped")

    def _listen(self, host: str):
        sock = socket.create_connection((host, PORT), timeout=CONNECT_TIMEOUT)
        self._sock = sock
        sock.settimeout(IDLE_SECONDS)
        sock.sendall(b"heos://system/register_for_change_events?enable=on\r\n")
        buffer, beat_pending = b"", False
        while not self._stop.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if beat_pending:
                    raise ConnectionError(f"no answer to a heart beat in {IDLE_SECONDS} s")
                sock.sendall(b"heos://system/heart_beat\r\n")
                beat_pending = True
                continue
            if not chunk:
                raise ConnectionError("HEOS closed the event connection")
            buffer += chunk
            while b"\r\n" in buffer:
                line, buffer = buffer.split(b"\r\n", 1)
                if not line.strip():
                    continue
                beat_pending = False
                self._handle(json.loads(line))

    def _handle(self, answer: dict):
        command = (answer.get("heos") or {}).get("command", "")
        if command == "system/register_for_change_events":
            self._set_connected((answer.get("heos") or {}).get("result") == "success")
            return
        if not command.startswith("event/"):
            return   # the heart beat's answer
        name = command[len("event/"):]
        if name == "player_now_playing_progress":
            return   # the playing position, every second: nothing the prompt shows
        try:
            self._on_event(name, message_fields(answer))
        except Exception as e:
            error(f"HEOS events: handling {name} failed: {e}")
