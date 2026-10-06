"""The websocket to a Home Assistant server (plans/HOME_ASSISTANT_PLAN.md §0): transport only.
One connection on its own asyncio thread — the auth handshake, a strictly increasing message id
per connection, `call()` correlating results to requests from any thread, `subscribe()` routing
event messages to handlers, reconnecting with backoff when the socket drops. What the messages
mean (states, services, registries) is HomeAssistantIntegration's business; the one thing this
class knows about Home Assistant is its protocol."""
import asyncio
import concurrent.futures
import json
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from websockets.asyncio.client import connect

from common.logging import error, info, trace

CALL_TIMEOUT = 10
OPEN_TIMEOUT = 10
RECONNECT_MIN = 1
RECONNECT_MAX = 60
STOP_TIMEOUT = 5


class NotConnected(Exception):
    """The socket is down: the call was not sent, or its answer never came because the
    connection dropped underneath it."""


class AuthRejected(NotConnected):
    """The server answered the token with auth_invalid; reconnecting would only repeat it."""


class HAError(Exception):
    """The server answered a command with success: false — the message is what a human
    (or the LLM) should read."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def websocket_url(base_url: str) -> str:
    """`http://host:8123` (or `host:8123`, or `https://...`) → `ws(s)://host:8123/api/websocket`."""
    url = base_url.strip().rstrip("/")
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/api/websocket"


class HomeAssistantClient:

    def __init__(self, base_url: str, token: str):
        self.url = websocket_url(base_url)
        self._token = token
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}    # event_type → handlers
        self._subscription_types: dict[int, str] = {}                   # subscription id → event_type
        self._refused: set[str] = set()                                 # event types the server would not give us
        self._on_connect: list[Callable[[], None]] = []
        self._stop_event: Optional[asyncio.Event] = None
        self._connected = threading.Event()
        self._ready = threading.Event()
        self.last_error: Optional[str] = None
        self.ha_version: Optional[str] = None
        self.disconnected_at: Optional[float] = None
        self.auth_failed = False
        self.reconnect_min = RECONNECT_MIN

    # --- lifecycle, from any thread ------------------------------------------------------------

    def start(self):
        """Start the connection thread; a no-op while it runs."""
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()
        self.auth_failed = False
        self._thread = threading.Thread(target=self._thread_main, name="HomeAssistantClient", daemon=True)
        self._thread.start()

    def stop(self):
        """Close the socket and end the thread; every pending call fails with NotConnected."""
        loop, thread = self._loop, self._thread
        if loop is None or thread is None or not thread.is_alive():
            return
        loop.call_soon_threadsafe(self._request_stop)
        thread.join(STOP_TIMEOUT)
        if thread.is_alive():
            error("Home Assistant client: the connection thread did not stop in time")
        self._loop = None
        self._thread = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def wait_connected(self, timeout: float) -> bool:
        return self._connected.wait(timeout)

    def wait_ready(self, timeout: float) -> bool:
        """Connected and the on_connect hooks have run (the mirror is filled)."""
        return self._ready.wait(timeout)

    def on_connect(self, hook: Callable[[], None]):
        """Runs on a worker thread after every successful handshake, before the client counts
        as ready; it may `call()`. Its exception is logged and does not break the connection."""
        self._on_connect.append(hook)

    def subscribe(self, event_type: str, handler: Callable[[dict], None]):
        """`handler(event)` for every event of that type, on the client thread — it must not
        block and must not `call()`. (Re)subscribed on every connection."""
        self._handlers.setdefault(event_type, []).append(handler)
        if self.connected and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._subscribe(event_type), self._loop)

    def refused(self, event_type: str) -> bool:
        return event_type in self._refused

    def call(self, type: str, timeout: float = CALL_TIMEOUT, **payload) -> Any:
        """Send `{"id", "type", **payload}` and return its `result`; `pong` returns None.
        Raises HAError on success: false, TimeoutError, NotConnected."""
        loop = self._loop
        if loop is None or not self.connected:
            raise NotConnected(self.last_error or "not connected")
        future = asyncio.run_coroutine_threadsafe(self._send(type, payload), loop)
        try:
            message = future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"{type} timed out after {timeout}s") from None
        if message.get("type") == "pong":
            return None
        if not message.get("success", False):
            err = message.get("error") or {}
            raise HAError(err.get("code", "unknown"), err.get("message", "unknown error"))
        return message.get("result")

    # --- the thread ------------------------------------------------------------------------------

    def _thread_main(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    def _request_stop(self):
        if self._stop_event is not None:
            self._stop_event.set()
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())

    async def _run(self):
        self._stop_event = asyncio.Event()
        delay = self.reconnect_min
        while not self._stop_event.is_set():
            try:
                await self._session()
                delay = self.reconnect_min
            except AuthRejected as e:
                self.last_error = str(e)
                self.auth_failed = True
                error(f"Home Assistant: {e} — not reconnecting until the integration is restarted")
                return
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                trace(f"Home Assistant: connection to {self.url} failed: {self.last_error}")
            if self._stop_event.is_set():
                return
            trace(f"Home Assistant: reconnecting in {delay}s")
            try:
                await asyncio.wait_for(self._stop_event.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, RECONNECT_MAX)

    async def _session(self):
        async with connect(self.url, open_timeout=OPEN_TIMEOUT, max_size=None) as ws:
            await self._handshake(ws)
            self._ws = ws
            self._next_id = 1
            self._connected.set()
            info(f"Home Assistant: connected to {self.url} (version {self.ha_version})")
            reader = asyncio.ensure_future(self._read(ws))
            setup = asyncio.ensure_future(self._setup())
            try:
                # Only the reader resolves a reply, so a socket that dies while the setup
                # waits for one would park the session forever: the first of the two to end
                # decides, and a reader that ends first takes the session down with it.
                await asyncio.wait({reader, setup}, return_when=asyncio.FIRST_COMPLETED)
                if setup.done():
                    setup.result()
                else:
                    info("Home Assistant: the connection dropped during setup — reconnecting")
                await reader
            finally:
                setup.cancel()
                reader.cancel()
                self._drop()

    async def _setup(self):
        """(Re)subscribe, fill the mirror through the hooks, then count as ready."""
        for event_type in list(self._handlers):
            await self._subscribe(event_type)
        await asyncio.get_running_loop().run_in_executor(None, self._run_hooks)
        self._ready.set()

    async def _handshake(self, ws):
        first = json.loads(await ws.recv())
        if first.get("type") != "auth_required":
            raise RuntimeError(f"expected auth_required, got {first.get('type')}")
        await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        reply = json.loads(await ws.recv())
        if reply.get("type") == "auth_invalid":
            raise AuthRejected(f"token rejected ({reply.get('message', 'auth_invalid')}) — create a new "
                               "long-lived access token (Profile → Security) and set home_assistant.token")
        if reply.get("type") != "auth_ok":
            raise RuntimeError(f"expected auth_ok, got {reply.get('type')}")
        self.ha_version = reply.get("ha_version")

    def _run_hooks(self):
        for hook in self._on_connect:
            try:
                hook()
            except Exception as e:
                error(f"Home Assistant: on_connect hook {getattr(hook, '__name__', hook)} failed: "
                      f"{type(e).__name__}: {e}")

    async def _read(self, ws):
        async for raw in ws:
            try:
                message = json.loads(raw)
            except ValueError:
                trace(f"Home Assistant: unparseable message: {raw[:120]!r}")
                continue
            kind = message.get("type")
            if kind in ("result", "pong"):
                future = self._pending.pop(message.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(message)
            elif kind == "event":
                self._dispatch(message)
            else:
                trace(f"Home Assistant: unexpected message type {kind!r}")

    def _dispatch(self, message: dict):
        event_type = self._subscription_types.get(message.get("id"))
        if event_type is None:
            trace(f"Home Assistant: event for unknown subscription {message.get('id')}")
            return
        for handler in self._handlers.get(event_type, ()):
            try:
                handler(message["event"])
            except Exception as e:
                error(f"Home Assistant: {event_type} handler failed: {type(e).__name__}: {e}")

    async def _send(self, type: str, payload: dict) -> dict:
        if self._ws is None:
            raise NotConnected(self.last_error or "not connected")
        message_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._ws.send(json.dumps({"id": message_id, "type": type, **payload}))
            return await future
        finally:
            self._pending.pop(message_id, None)

    async def _subscribe(self, event_type: str):
        try:
            reply = await self._send("subscribe_events", {"event_type": event_type})
        except NotConnected:
            return
        if reply.get("success"):
            self._subscription_types[reply["id"]] = event_type
            self._refused.discard(event_type)
            trace(f"Home Assistant: subscribed to {event_type}")
        else:
            err = reply.get("error") or {}
            if event_type not in self._refused:
                info(f"Home Assistant: subscription to {event_type} refused ({err.get('code')}: "
                     f"{err.get('message')}) — the token's user is probably not an admin")
            self._refused.add(event_type)

    def _drop(self):
        was_connected = self.connected
        self._connected.clear()
        self._ready.clear()
        self._ws = None
        self._subscription_types.clear()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(NotConnected("connection dropped"))
        self._pending.clear()
        if was_connected:
            self.disconnected_at = time.time()
            info(f"Home Assistant: disconnected from {self.url}")
