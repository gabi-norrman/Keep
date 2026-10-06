"""Home Assistant as the HomeAutomation backend, an Integration and a Feed
(plans/HOME_ASSISTANT_PLAN.md). One websocket (HomeAssistantClient) does nearly everything:
`get_states` fills a live state mirror at connect and every `state_changed` event keeps it
current, so `snapshot()` is a read from memory; the registries (areas, entities, devices) and
the Assist exposure list come over the same socket and are refreshed on a registry event or
after REGISTRY_TTL; the commands are `call_service` through a domain → service table; the
health check is a `ping`. The event handler also queues the changes the dialog wants to know
about (HomeAssistantModel.feed_entry) for `fetch`. REST is used for one thing the websocket
does not offer, `GET /api/error_log`."""
import queue
import threading
import time
from typing import List, Optional

import requests

from .HomeAssistantClient import HAError, HomeAssistantClient, NotConnected
from .HomeAssistantModel import (
    ACTION_DOMAINS, ASSISTANT, LIGHT_DOMAINS, NameIndex, Registries, Unresolved, build, domain_of, feed_entry)
from assistant.integration.HomeAutomation import HomeAutomation, HomeSnapshot
from assistant.integration.Feed import Feed, FeedBatch, FeedEntry
from assistant.integration.Integration import Integration
from common.ConfigManager import config
from common.logging import info, trace

POLL_INTERVAL = 2               # seconds between drains of the live event queue
CONNECT_WAIT = 10               # seconds a snapshot or command waits for the socket to come up
REGISTRY_TTL = 600              # seconds between registry refreshes when no registry event arrives
ERROR_LOG_LINES = 80
REGISTRY_EVENTS = ("entity_registry_updated", "area_registry_updated", "device_registry_updated")

# domain → (service domain, service) per command; a domain missing from a table is the LLM
# using the wrong command, and the answer says which one to use instead.
TURN_ON = {"light": ("light", "turn_on"), "switch": ("switch", "turn_on"), "fan": ("fan", "turn_on"),
           "cover": ("cover", "open_cover"), "lock": ("lock", "lock"), "input_boolean": ("input_boolean", "turn_on"),
           "automation": ("automation", "turn_on"), "media_player": ("media_player", "turn_on"),
           "climate": ("climate", "turn_on")}
TURN_OFF = {"light": ("light", "turn_off"), "switch": ("switch", "turn_off"), "fan": ("fan", "turn_off"),
            "cover": ("cover", "close_cover"), "lock": ("lock", "unlock"), "input_boolean": ("input_boolean", "turn_off"),
            "automation": ("automation", "turn_off"), "media_player": ("media_player", "turn_off"),
            "climate": ("climate", "turn_off")}
DIM = {"light": ("light", "turn_on", "brightness_pct"), "fan": ("fan", "set_percentage", "percentage"),
       "cover": ("cover", "set_cover_position", "position")}
EXECUTE = {"scene": ("scene", "turn_on"), "script": ("script", "turn_on"), "automation": ("automation", "trigger"),
           "button": ("button", "press"), "input_button": ("input_button", "press")}


class HomeAssistantIntegration(Integration, Feed, HomeAutomation):

    poll_interval = POLL_INTERVAL
    item_noun = "events"
    section_filters = {}

    def __init__(self):
        super().__init__()
        self._client: Optional[HomeAssistantClient] = None
        self._lock = threading.Lock()
        self._fill_lock = threading.Lock()
        self._filled = False            # the mirror holds a get_states of the current connection
        self._states: dict[str, dict] = {}
        self._registries = Registries()
        self._registries_at = 0.0
        self._registries_stale = True
        self._snapshot = HomeSnapshot()
        self._index = NameIndex()
        self._dirty = True
        self._users: dict[str, str] = {}
        self._live: "queue.Queue[FeedEntry]" = queue.Queue()

    def _on_reset(self):
        from assistant.integration.FeedRunner import Feeds
        Feeds.get_instance().stop(self.feed_name)
        if self._client is not None:
            self._client.stop()
            self._client = None

    # --- Integration ------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Home Assistant"

    @property
    def config_keys(self) -> List[str]:
        return ["home_assistant.url", "home_assistant.token"]

    @property
    def config_section(self) -> str:
        return "home_assistant"

    @property
    def base_url(self) -> str:
        url = str(config.get("home_assistant.url", "") or "").strip().rstrip("/")
        return url if "://" in url else f"http://{url}"

    @property
    def exposed_only(self) -> bool:
        return bool(config.get("home_assistant.exposed_only", True))

    def check_health(self) -> bool:
        client = self.client()
        if client.auth_failed:
            raise RuntimeError(client.last_error)
        if not client.wait_connected(CONNECT_WAIT):
            raise RuntimeError(client.last_error or f"no connection to {client.url}")
        client.call("ping")
        return True

    # --- the client and the mirror ----------------------------------------------------------------

    def client(self) -> HomeAssistantClient:
        """The websocket, built from config and started on first use."""
        if self._client is None:
            client = HomeAssistantClient(self.base_url, str(config.get("home_assistant.token", "") or ""))
            client.subscribe("state_changed", self._on_state_changed)
            for event_type in REGISTRY_EVENTS:
                client.subscribe(event_type, self._on_registry_event)
            client.on_connect(self._refill)
            self._client = client
            client.start()
        return self._client

    def _refill(self):
        """After every handshake: the whole state table, then the registries. The client
        logs a failure here and counts as ready anyway, so `_filled` stays false until the
        table landed and `_current` fills it on the next snapshot or command instead."""
        with self._fill_lock:
            self._filled = False
            states = self._client.call("get_states")
            with self._lock:
                self._states = {state["entity_id"]: state for state in states}
                self._dirty = True
                self._filled = True
            info(f"Home Assistant: {len(states)} states loaded")
            self._load_registries()

    def _load_registries(self):
        """Areas, entities, devices, the Assist exposure list and the users, each kept from the
        last time when the server refuses it now (a token that is not an admin's)."""
        client = self._client
        registries = Registries(expose_new=self._registries.expose_new)
        loaded = []
        for attr, command, key in (("areas", "config/area_registry/list", "area_id"),
                                   ("entities", "config/entity_registry/list", "entity_id"),
                                   ("devices", "config/device_registry/list", "id")):
            try:
                entries = client.call(command)
                setattr(registries, attr, {entry[key]: entry for entry in entries})
                loaded.append(f"{len(entries)} {attr}")
            except HAError as e:
                info(f"Home Assistant: {command} refused ({e.code}: {e.message}) — keeping the last {attr}")
                setattr(registries, attr, getattr(self._registries, attr))
        try:
            registries.exposed = client.call("homeassistant/expose_entity/list").get("exposed_entities", {})
            registries.expose_new = bool(client.call("homeassistant/expose_new_entities/get",
                                                     assistant=ASSISTANT).get("expose_new", True))
            loaded.append(f"{len(registries.exposed)} exposure choices")
        except HAError as e:
            info(f"Home Assistant: exposure list refused ({e.code}: {e.message}) — keeping the last one")
            registries.exposed = self._registries.exposed
        try:
            self._users = {user["id"]: user.get("name") or user["id"] for user in client.call("config/auth/list")}
        except HAError as e:
            trace(f"Home Assistant: user list refused ({e.code}) — feed rows will not name who acted")
        with self._lock:
            self._registries = registries
            self._registries_at = time.time()
            self._registries_stale = False
            self._dirty = True
        info(f"Home Assistant: registries loaded ({', '.join(loaded) or 'nothing'})")

    def _on_state_changed(self, event: dict):
        data = event.get("data") or {}
        entity_id = data.get("entity_id")
        old, new = data.get("old_state"), data.get("new_state")
        if not entity_id:
            return
        with self._lock:
            if new is None:
                self._states.pop(entity_id, None)
            else:
                self._states[entity_id] = new
            self._dirty = True
            index = self._index
        user = None
        context = (new or {}).get("context") or {}
        if context.get("user_id"):
            user = self._users.get(context["user_id"])
        entry = feed_entry(old, new, index, user)
        if entry is not None:
            trace(f"Home Assistant event: {entry.content}")
            self._live.put(entry)

    def _on_registry_event(self, event: dict):
        trace(f"Home Assistant: {event.get('event_type')} — registries will be refreshed")
        with self._lock:
            self._registries_stale = True

    def _current(self) -> tuple[HomeSnapshot, NameIndex]:
        """The snapshot and index, rebuilt from the mirror when it changed since; the
        registries are reloaded first when an event marked them stale or the TTL passed."""
        client = self.client()
        if client.auth_failed:
            raise RuntimeError(client.last_error)
        if not client.wait_ready(CONNECT_WAIT):
            since = time.strftime("%H:%M", time.localtime(client.disconnected_at)) if client.disconnected_at else "start"
            raise RuntimeError(f"Home Assistant websocket down since {since}: {client.last_error or 'connecting'}")
        if not self._filled:
            info("Home Assistant: the state mirror did not fill at connect — filling it now")
            self._refill()
        with self._lock:
            stale = self._registries_stale or time.time() - self._registries_at > REGISTRY_TTL
        if stale:
            self._load_registries()
        with self._lock:
            if self._dirty:
                self._snapshot, self._index = build(self._states, self._registries, self.exposed_only)
                self._snapshot.taken_at = time.time()
                self._dirty = False
            return self._snapshot, self._index

    # --- HomeAutomation: state ------------------------------------------------------------------

    def snapshot(self) -> HomeSnapshot:
        snapshot, _ = self._current()
        return snapshot

    def error_logs(self) -> str:
        response = requests.get(f"{self.base_url}/api/error_log", timeout=10,
                                headers={"Authorization": f"Bearer {config.get('home_assistant.token')}"})
        response.raise_for_status()
        lines = [line for line in response.text.strip().split("\n") if line.strip()]
        if not lines:
            info("Home Assistant: the error log is empty")
            return "Home Assistant reported no errors."
        text = HomeAutomation.to_display("\n".join(lines[-ERROR_LOG_LINES:]))
        info(f"Home Assistant error log: {len(lines)} lines, showing the last {min(len(lines), ERROR_LOG_LINES)}")
        return f"## HOME ASSISTANT ERROR LOG\n{text}"

    # --- HomeAutomation: commands ---------------------------------------------------------------

    def turn_on(self, name: str) -> str:
        return self._command(name, TURN_ON, "turn_on")

    def turn_off(self, name: str) -> str:
        return self._command(name, TURN_OFF, "turn_off")

    def dim(self, name: str, level: int) -> str:
        level = int(level)
        if level <= 0:
            return self.turn_off(name)
        level = min(level, 100)
        entity_id, display = self._resolve(name)
        if entity_id is None:
            return display
        route = DIM.get(domain_of(entity_id))
        if route is None:
            return f"{display} is not dimmable."
        domain, service, field = route
        return self._call_service(domain, service, entity_id, display, {field: level})

    def execute(self, name: str) -> str:
        return self._command(name, EXECUTE, "execute")

    def _command(self, name: str, table: dict, verb: str) -> str:
        entity_id, display = self._resolve(name)
        if entity_id is None:
            return display
        route = table.get(domain_of(entity_id))
        if route is None:
            return self._wrong_command(entity_id, display, verb)
        return self._call_service(route[0], route[1], entity_id, display)

    @staticmethod
    def _wrong_command(entity_id: str, display: str, verb: str) -> str:
        domain = domain_of(entity_id)
        if domain in ACTION_DOMAINS:
            return f"{display} is a {domain}; use execute."
        if domain in LIGHT_DOMAINS or domain == "input_boolean":
            return f"{display} is a {domain}; use turn_on / turn_off."
        return f"{display} is a {domain} and cannot be controlled with {verb}."

    def _resolve(self, name: str) -> tuple[Optional[str], str]:
        """(entity_id, its display name), or (None, the text for the LLM)."""
        try:
            _, index = self._current()
            entity_id = index.resolve(name)
            return entity_id, index.display(entity_id) or name
        except Unresolved as e:
            info(f"Home Assistant: {e}")
            return None, str(e)
        except TimeoutError as e:
            info(f"Home Assistant: cannot resolve '{name}': {e}")
            return None, "Request timed out"
        except HAError as e:
            info(f"Home Assistant: cannot resolve '{name}': {e.code}: {e.message}")
            return None, e.message
        except (RuntimeError, NotConnected) as e:
            info(f"Home Assistant: cannot resolve '{name}': {e}")
            return None, "Could not connect to Home Assistant"

    def _call_service(self, domain: str, service: str, entity_id: str, display: str, data: Optional[dict] = None) -> str:
        payload = {"domain": domain, "service": service, "target": {"entity_id": entity_id}}
        if data:
            payload["service_data"] = data
        try:
            self.client().call("call_service", **payload)
        except HAError as e:
            info(f"Home Assistant: {domain}.{service} on {entity_id} failed: {e.code}: {e.message}")
            return e.message
        except TimeoutError:
            return "Request timed out"
        except NotConnected:
            return "Could not connect to Home Assistant"
        except Exception as e:
            return str(e)
        info(f"Home Assistant: {domain}.{service} → {display}{f' {data}' if data else ''}")
        return ""

    # --- Feed ---------------------------------------------------------------------------------------

    @property
    def feed_name(self) -> str:
        return "Home Assistant"

    @property
    def config_slug(self) -> str:
        return "home_assistant"

    def initial_cursor(self, horizon_ts: float) -> Optional[str]:
        return None

    def open_feed(self) -> bool:
        self.client()
        return True

    def fetch(self, cursor: Optional[str], backfill: bool) -> FeedBatch:
        """Whatever the event handler queued since the last drain; nothing is ever replayed."""
        entries: list[FeedEntry] = []
        while True:
            try:
                entries.append(self._live.get_nowait())
            except queue.Empty:
                break
        return FeedBatch(entries)
