"""HomeAssistantIntegration with the websocket client faked: the contract, the mirror and the
snapshot, the commands' service calls and answers, the error log, the health check and the
feed (plans/HOME_ASSISTANT_PLAN.md)."""
import json
import queue
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from integrations.home_assistant.HomeAssistantClient import HAError, NotConnected
from integrations.home_assistant.HomeAssistantIntegration import HomeAssistantIntegration, ERROR_LOG_LINES, REGISTRY_EVENTS, REGISTRY_TTL
from assistant.integration.HomeAutomation import render

MODULE = "integrations.home_assistant.HomeAssistantIntegration"
DATA = Path(__file__).parents[2] / "data" / "home" / "ha"
CONFIG = {"home_assistant.url": "http://ha.local:8123/", "home_assistant.token": "tok", "home_assistant.exposed_only": True}


class FakeClient:
    """Answers `call` from a table, remembers every call, and lets a test flip its state."""

    def __init__(self):
        raw = json.loads((DATA / "registries.json").read_text())
        self.results = {
            "get_states": json.loads((DATA / "states.json").read_text()),
            "config/area_registry/list": raw["areas"],
            "config/entity_registry/list": raw["entities"],
            "config/device_registry/list": raw["devices"],
            "homeassistant/expose_entity/list": {"exposed_entities": raw["exposed"]},
            "homeassistant/expose_new_entities/get": {"expose_new": raw["expose_new"]},
            "config/auth/list": [{"id": "u1", "name": "Marcus"}],
            "call_service": {"context": {"id": "ctx"}},
            "ping": None,
        }
        self.calls: list[tuple[str, dict]] = []
        self.handlers: dict[str, list] = {}
        self.hooks = []
        self.url = "ws://ha.local:8123/api/websocket"
        self.connected = True
        self.ready = True
        self.auth_failed = False
        self.last_error = None
        self.disconnected_at = None
        self.started = 0
        self.stopped = 0

    def subscribe(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def on_connect(self, hook):
        self.hooks.append(hook)

    def start(self):
        self.started += 1
        if self.connected:      # the real client runs the hooks only after auth_ok
            for hook in self.hooks:
                try:
                    hook()
                except Exception:   # logged and swallowed by the real client, which counts as ready anyway
                    pass

    def stop(self):
        self.stopped += 1

    def wait_connected(self, timeout):
        return self.connected

    def wait_ready(self, timeout):
        return self.ready

    def call(self, type, timeout=10, **payload):
        self.calls.append((type, payload))
        if not self.connected:
            raise NotConnected("not connected")
        result = self.results.get(type, KeyError)
        if result is KeyError:
            raise HAError("unknown_command", f"Unknown command {type}")
        if isinstance(result, Exception):
            raise result
        return result

    def push(self, event_type, data):
        for handler in self.handlers.get(event_type, ()):
            handler({"event_type": event_type, "data": data})


def state(entity_id, value, user_id=None, **attributes):
    return {"entity_id": entity_id, "state": value, "attributes": attributes,
            "last_changed": "2026-09-15T10:00:00+00:00", "last_updated": "2026-09-15T10:00:00+00:00",
            "context": {"id": "c", "parent_id": None, "user_id": user_id}}


@pytest.fixture
def cfg():
    return dict(CONFIG)


@pytest.fixture
def integration(cfg):
    HomeAssistantIntegration.reset_instance()
    with patch(f"{MODULE}.config") as config:
        config.get.side_effect = lambda key, default=None: cfg.get(key, default)
        yield HomeAssistantIntegration()
    HomeAssistantIntegration.reset_instance()


@pytest.fixture
def fake(integration):
    client = FakeClient()
    with patch(f"{MODULE}.HomeAssistantClient", return_value=client):
        yield client


@pytest.mark.unit
class TestContract:
    def test_base_url_is_normalised(self, integration, cfg):
        assert integration.base_url == "http://ha.local:8123"
        cfg["home_assistant.url"] = "ha.local:8123"
        assert integration.base_url == "http://ha.local:8123"

@pytest.mark.unit
class TestClientWiring:
    def test_client_is_built_from_config_started_once_and_wired(self, integration, fake):
        with patch(f"{MODULE}.HomeAssistantClient", return_value=fake) as cls:
            assert integration.client() is fake
            assert integration.client() is fake
        cls.assert_called_once_with("http://ha.local:8123", "tok")
        assert fake.started == 1
        assert set(fake.handlers) == {"state_changed", *REGISTRY_EVENTS}
        assert fake.hooks == [integration._refill]

    def test_refill_loads_states_registries_exposure_and_users(self, integration, fake):
        integration.client()
        kinds = [c[0] for c in fake.calls]
        assert kinds[:1] == ["get_states"]
        assert {"config/area_registry/list", "config/entity_registry/list", "config/device_registry/list",
                "homeassistant/expose_entity/list", "homeassistant/expose_new_entities/get", "config/auth/list"} <= set(kinds)
        assert ("homeassistant/expose_new_entities/get", {"assistant": "conversation"}) in fake.calls
        assert integration._users == {"u1": "Marcus"}
        assert len(integration._states) == 25
        assert integration._registries.areas["kok"]["name"] == "Kök"

    def test_refused_registries_keep_the_last_ones(self, integration, fake):
        integration.client()
        fake.results["config/area_registry/list"] = HAError("unauthorized", "Unauthorized")
        fake.results["homeassistant/expose_entity/list"] = HAError("unauthorized", "Unauthorized")
        fake.results["config/auth/list"] = HAError("unauthorized", "Unauthorized")
        integration._load_registries()
        assert integration._registries.areas["kok"]["name"] == "Kök"
        assert integration._registries.exposed["sensor.power"] == {"conversation": True}

    def test_a_failed_fill_at_connect_is_retried_by_the_next_snapshot(self, integration, fake):
        """The client swallows a failing on_connect hook and counts as ready: without a retry
        the mirror would stay empty (an empty house) until the next socket drop."""
        fake.results["get_states"] = TimeoutError("get_states timed out after 10s")
        integration.client()
        assert integration._filled is False
        with pytest.raises(TimeoutError):
            integration.snapshot()
        fake.results["get_states"] = json.loads((DATA / "states.json").read_text())
        assert integration.snapshot().is_empty() is False
        assert integration._filled is True
        assert [c for c, _ in fake.calls].count("get_states") == 3

    def test_a_refill_that_fails_marks_the_mirror_unfilled(self, integration, fake):
        integration.client()
        assert integration._filled is True
        fake.results["get_states"] = NotConnected("connection dropped")
        with pytest.raises(NotConnected):
            integration._refill()
        assert integration._filled is False

    def test_reset_stops_the_feed_and_the_client(self, integration, fake):
        integration.client()
        with patch("assistant.integration.FeedRunner.Feeds.get_instance") as feeds:
            integration._on_reset()
        feeds.return_value.stop.assert_called_once_with("Home Assistant")
        assert fake.stopped == 1 and integration._client is None


@pytest.mark.unit
class TestSnapshot:
    def test_snapshot_renders_the_golden_text(self, integration, fake):
        snapshot = integration.snapshot()
        assert render(snapshot) == (DATA / "rendered.md").read_text()
        assert snapshot.taken_at > 0
        assert snapshot.flag("Night") is True

    def test_snapshot_is_a_memory_read_after_the_first(self, integration, fake):
        integration.snapshot()
        calls = len(fake.calls)
        integration.snapshot()
        assert len(fake.calls) == calls

    def test_events_update_the_mirror_and_rebuild_lazily(self, integration, fake):
        integration.snapshot()
        fake.push("state_changed", {"entity_id": "light.hall", "old_state": state("light.hall", "on"),
                                    "new_state": state("light.hall", "off", friendly_name="Hall light")})
        fake.push("state_changed", {"entity_id": "light.new", "old_state": None,
                                    "new_state": state("light.new", "on", friendly_name="New lamp")})
        fake.push("state_changed", {"entity_id": "light.garage", "old_state": state("light.garage", "unavailable"),
                                    "new_state": None})
        lights = {e.name: e.value for e in integration.snapshot().lights}
        assert lights["Hall light"] == "off" and lights["New lamp"] == "on" and "Garage light" not in lights

    def test_registry_event_reloads_the_registries_on_the_next_snapshot(self, integration, fake):
        integration.snapshot()
        fake.results["config/area_registry/list"] = [{"area_id": "hall", "name": "Hallway", "aliases": []}]
        fake.push("area_registry_updated", {"action": "update", "area_id": "hall"})
        calls = len(fake.calls)
        lights = {e.name: e.area for e in integration.snapshot().lights}
        assert lights["Hall light"] == "Hallway"
        assert len(fake.calls) > calls

    def test_registries_reload_after_the_ttl(self, integration, fake):
        integration.snapshot()
        with patch(f"{MODULE}.time.time", return_value=integration._registries_at + REGISTRY_TTL + 1):
            integration.snapshot()
        assert [c[0] for c in fake.calls].count("config/area_registry/list") == 2

    def test_snapshot_raises_while_the_socket_is_down(self, integration, fake):
        fake.ready = False
        fake.disconnected_at = 1_789_466_400.0
        fake.last_error = "ConnectionRefusedError"
        with pytest.raises(RuntimeError, match="websocket down since .*ConnectionRefusedError"):
            integration.snapshot()

    def test_snapshot_raises_with_the_auth_error(self, integration, fake):
        fake.auth_failed = True
        fake.last_error = "token rejected"
        with pytest.raises(RuntimeError, match="token rejected"):
            integration.snapshot()

    def test_exposed_only_false_shows_everything(self, integration, fake, cfg):
        cfg["home_assistant.exposed_only"] = False
        names = {e.name for e in integration.snapshot().lights}
        assert "Bedroom lamp" in names


@pytest.mark.unit
class TestCommands:
    def last_service(self, fake):
        return [c for c in fake.calls if c[0] == "call_service"][-1][1]

    def test_turn_on_and_off_by_domain(self, integration, fake):
        assert integration.turn_on("Hall light (Hall)") == ""
        assert self.last_service(fake) == {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.hall"}}
        assert integration.turn_off("Blinds") == ""
        assert self.last_service(fake) == {"domain": "cover", "service": "close_cover", "target": {"entity_id": "cover.blinds"}}
        assert integration.turn_on("Front door lock") == ""
        assert self.last_service(fake)["service"] == "lock"
        assert integration.turn_off("Front door lock") == ""
        assert self.last_service(fake)["service"] == "unlock"
        assert integration.turn_on("Night") == ""
        assert self.last_service(fake) == {"domain": "input_boolean", "service": "turn_on", "target": {"entity_id": "input_boolean.night"}}
        assert integration.turn_off("Sunset lights") == ""
        assert self.last_service(fake)["domain"] == "automation" and self.last_service(fake)["service"] == "turn_off"

    def test_dim_by_domain_and_clamping(self, integration, fake):
        assert integration.dim("Hall light [Hall]", 40) == ""
        assert self.last_service(fake) == {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.hall"},
                                           "service_data": {"brightness_pct": 40}}
        assert integration.dim("Bedroom fan", 150) == ""
        assert self.last_service(fake)["service_data"] == {"percentage": 100}
        assert integration.dim("Blinds", "25") == ""
        assert self.last_service(fake) == {"domain": "cover", "service": "set_cover_position",
                                           "target": {"entity_id": "cover.blinds"}, "service_data": {"position": 25}}

    def test_dim_to_zero_turns_off(self, integration, fake):
        assert integration.dim("Hall light", 0) == ""
        assert self.last_service(fake)["service"] == "turn_off"

    def test_dim_on_something_not_dimmable(self, integration, fake):
        assert integration.dim("Front door lock", 50) == "Front door lock [Hall] is not dimmable."
        assert not [c for c in fake.calls if c[0] == "call_service"]

    def test_execute_by_domain(self, integration, fake):
        assert integration.execute("Movie night") == ""
        assert self.last_service(fake) == {"domain": "scene", "service": "turn_on", "target": {"entity_id": "scene.movie_night"}}
        assert integration.execute("Goodnight") == ""
        assert self.last_service(fake)["domain"] == "script"
        assert integration.execute("Sunset lights") == ""
        assert self.last_service(fake)["service"] == "trigger"


    def test_the_domains_rendered_as_sensors_still_switch(self, integration, fake):
        """A thermostat or a speaker is listed under SENSORS, yet turn_on / turn_off act on
        it — the pairing a refactor of the tables would break first."""
        for name, domain in (("Kitchen spots switch", "switch"), ("Bedroom fan", "fan"),
                             ("Thermostat", "climate"), ("Living room speaker", "media_player")):
            assert integration.turn_on(name) == ""
            assert (self.last_service(fake)["domain"], self.last_service(fake)["service"]) == (domain, "turn_on")
            assert integration.turn_off(name) == ""
            assert (self.last_service(fake)["domain"], self.last_service(fake)["service"]) == (domain, "turn_off")

    def test_a_button_is_pressed(self, integration, fake, cfg):
        cfg["home_assistant.exposed_only"] = False    # a button is not exposed to Assist by default
        integration.snapshot()
        with integration._lock:
            integration._states["button.doorbell"] = state("button.doorbell", "unknown", friendly_name="Doorbell")
            integration._dirty = True
        assert integration.execute("Doorbell") == ""
        assert self.last_service(fake) == {"domain": "button", "service": "press", "target": {"entity_id": "button.doorbell"}}
        assert integration.turn_on("Doorbell") == "Doorbell is a button; use execute."

    def test_the_wrong_command_points_at_the_right_one(self, integration, fake):
        assert integration.execute("Hall light") == "Hall light [Hall] is a light; use turn_on / turn_off."
        assert integration.turn_on("Movie night") == "Movie night is a scene; use execute."
        assert integration.turn_on("Temperature (Kök)") == "Temperature [Kök] is a sensor and cannot be controlled with turn_on."
        assert not [c for c in fake.calls if c[0] == "call_service"]

    def test_unresolved_names_answer_with_the_index_text(self, integration, fake):
        assert integration.turn_on("Temperature") == '"Temperature" could be Temperature [Sovrum], Temperature [Kök] — say which.'
        assert integration.turn_on("Nigth") == 'No entity named "Nigth". Did you mean: Night?'

    def test_server_errors_come_back_as_text(self, integration, fake):
        integration.snapshot()
        fake.results["call_service"] = HAError("service_validation_error", "Validation error: brightness out of range")
        assert integration.dim("Hall light", 40) == "Validation error: brightness out of range"
        fake.results["call_service"] = TimeoutError("call_service timed out after 10s")
        assert integration.turn_on("Hall light") == "Request timed out"
        fake.results["call_service"] = NotConnected("connection dropped")
        assert integration.turn_on("Hall light") == "Could not connect to Home Assistant"

    @pytest.mark.parametrize("failure, expected", [
        (TimeoutError("config/entity_registry/list timed out after 10s"), "Request timed out"),
        (NotConnected("connection dropped"), "Could not connect to Home Assistant"),
    ])
    def test_a_registry_reload_failing_under_a_command_answers_with_text(self, integration, fake, failure, expected):
        """A stale registry is reloaded on the way to resolving a name; the reload's
        errors must become the LLM's answer, not escape the command."""
        integration.snapshot()
        fake.push("entity_registry_updated", {"action": "update"})
        fake.results["config/entity_registry/list"] = failure
        assert integration.turn_on("Hall light") == expected
        assert not any(c == "call_service" for c, _ in fake.calls)

    def test_commands_while_down_cannot_connect(self, integration, fake):
        fake.ready = False
        assert integration.turn_on("Hall light") == "Could not connect to Home Assistant"
        assert integration.execute("Goodnight") == "Could not connect to Home Assistant"


@pytest.mark.unit
class TestHealthAndErrorLog:
    def test_health_is_a_ping(self, integration, fake):
        assert integration.check_health() is True
        assert ("ping", {}) in fake.calls

    def test_health_raises_the_auth_error(self, integration, fake):
        fake.auth_failed = True
        fake.last_error = "token rejected (Invalid access token)"
        with pytest.raises(RuntimeError, match="token rejected"):
            integration.check_health()

    def test_health_raises_when_not_connected(self, integration, fake):
        fake.connected = False
        fake.last_error = "ConnectionRefusedError"
        with pytest.raises(RuntimeError, match="ConnectionRefusedError"):
            integration.check_health()

    def test_error_log_is_the_last_lines_over_rest(self, integration):
        lines = [f"2026-09-15 10:00:{i:02d} ERROR (MainThread) [homeassistant.components.hue] failure (attempt {i})" for i in range(100)]
        response = Mock(text="\n".join(lines) + "\n\n")
        with patch(f"{MODULE}.requests.get", return_value=response) as get:
            text = integration.error_logs()
        get.assert_called_once_with("http://ha.local:8123/api/error_log", timeout=10,
                                    headers={"Authorization": "Bearer tok"})
        body = text.split("\n")[1:]
        assert text.startswith("## HOME ASSISTANT ERROR LOG\n")
        assert len(body) == ERROR_LOG_LINES and body[-1].endswith("[attempt 99]")

    def test_empty_error_log(self, integration):
        with patch(f"{MODULE}.requests.get", return_value=Mock(text="\n")):
            assert integration.error_logs() == "Home Assistant reported no errors."

    def test_error_log_http_failure_raises(self, integration):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("401")
        with patch(f"{MODULE}.requests.get", return_value=response), pytest.raises(requests.HTTPError):
            integration.error_logs()


@pytest.mark.unit
class TestFeed:
    def test_open_starts_the_client_and_fetch_drains_the_queue(self, integration, fake):
        assert integration.open_feed() is True
        assert fake.started == 1
        assert integration.fetch(None, backfill=False).entries == []
        integration.snapshot()
        fake.push("state_changed", {"entity_id": "light.hall", "old_state": state("light.hall", "off"),
                                    "new_state": state("light.hall", "on", user_id="u1", brightness=51)})
        fake.push("state_changed", {"entity_id": "sensor.kitchen_temperature",
                                    "old_state": state("sensor.kitchen_temperature", "21.4"),
                                    "new_state": state("sensor.kitchen_temperature", "21.5")})
        batch = integration.fetch(None, backfill=False)
        assert [e.content for e in batch.entries] == ["Hall light [Hall] turned on 20% by Marcus"]
        assert batch.cursor is None and batch.exhausted
        assert integration.fetch(None, backfill=False).entries == []

    def test_an_unknown_user_id_names_nobody(self, integration, fake):
        integration.snapshot()
        fake.push("state_changed", {"entity_id": "light.hall", "old_state": state("light.hall", "off"),
                                    "new_state": state("light.hall", "on", user_id="ghost")})
        assert integration.fetch(None, backfill=False).entries[0].content == "Hall light [Hall] turned on"

    def test_events_before_the_first_snapshot_use_the_friendly_name(self, integration, fake):
        integration.client()
        fake.push("state_changed", {"entity_id": "light.hall", "old_state": state("light.hall", "off"),
                                    "new_state": state("light.hall", "on", friendly_name="Hall light")})
        assert integration.fetch(None, backfill=False).entries[0].content == "Hall light turned on"

    def test_a_malformed_event_is_ignored(self, integration, fake):
        integration.client()
        fake.push("state_changed", {})
        fake.push("state_changed", {"entity_id": "light.hall"})
        assert integration.fetch(None, backfill=False).entries == []
        assert isinstance(integration._live, queue.Queue)
