"""HueIntegration with the client faked: the snapshot, the commands' PUTs and answers, the
re-read on an unknown name, the bridge's failures as text, the health check."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from assistant.integration.HomeAutomation import render
from integrations.hue.HueClient import HueError
from integrations.hue.HueIntegration import HueIntegration

MODULE = "integrations.hue.HueIntegration"
DATA = Path(__file__).parents[2] / "data" / "home" / "hue"
CONFIG = {"hue.bridge": "192.0.2.10", "hue.app_key": "app-key"}


class FakeClient:
    def __init__(self, host, app_key):
        self.host, self.app_key = host, app_key
        self.resources = json.loads((DATA / "resources.json").read_text())
        self.resources["bridge"] = [{"id": "b-1", "bridge_id": "ecb5fa00"}]
        self.gets: list[str] = []
        self.puts: list[tuple[str, str, dict]] = []
        self.failure: Exception | None = None

    def get(self, rtype):
        if self.failure:
            raise self.failure
        self.gets.append(rtype)
        return self.resources.get(rtype, [])

    def put(self, rtype, rid, body):
        if self.failure:
            raise self.failure
        self.puts.append((rtype, rid, body))
        return []


@pytest.fixture
def integration():
    HueIntegration.reset_instance()
    with patch(f"{MODULE}.config") as cfg, patch(f"{MODULE}.HueClient", FakeClient):
        cfg.get.side_effect = lambda key, default=None: CONFIG.get(key, default)
        yield HueIntegration()
    HueIntegration.reset_instance()


@pytest.fixture
def fake(integration) -> FakeClient:
    return integration.client()


@pytest.mark.unit
class TestSnapshot:
    def test_renders_the_golden_text(self, integration):
        assert render(integration.snapshot()) == (DATA / "rendered.md").read_text()

    def test_the_client_is_built_from_config_once(self, integration):
        assert integration.client() is integration.client()
        assert (integration.client().host, integration.client().app_key) == ("192.0.2.10", "app-key")

    def test_reset_drops_the_client(self, integration, fake):
        integration._on_reset()
        assert integration.client() is not fake

    def test_a_failing_bridge_raises_so_the_section_keeps_its_last(self, integration, fake):
        fake.failure = requests.ConnectionError("down")
        with pytest.raises(requests.ConnectionError):
            integration.snapshot()


@pytest.mark.unit
class TestCommands:
    def test_turn_on_and_off_put_the_on_field(self, integration, fake):
        assert integration.turn_on("Taklampa [Kök]") == ""
        assert integration.turn_off("All lights [Sovrum]") == ""
        assert fake.puts == [("light", "l-tak", {"on": {"on": True}}), ("grouped_light", "g-sov", {"on": {"on": False}})]

    def test_dim_sets_the_brightness_and_turns_on(self, integration, fake):
        assert integration.dim("Taklampa (Kök)", "50") == ""
        assert fake.puts[-1] == ("light", "l-tak", {"on": {"on": True}, "dimming": {"brightness": 50}})

    def test_dim_clamps_to_100_and_zero_turns_off(self, integration, fake):
        integration.dim("Taklampa", 140)
        integration.dim("Taklampa", 0)
        assert [p[2] for p in fake.puts] == [{"on": {"on": True}, "dimming": {"brightness": 100}}, {"on": {"on": False}}]

    def test_execute_recalls_the_scene(self, integration, fake):
        assert integration.execute("Relax [Sovrum]") == ""
        assert fake.puts == [("scene", "s-relax", {"recall": {"action": "active"}})]

    def test_the_wrong_command_points_at_the_right_one(self, integration, fake):
        assert "use execute" in integration.turn_on("Relax")
        assert "use turn_on" in integration.execute("Taklampa")
        assert "cannot be controlled" in integration.dim("Hallsensor motion [Hall]", 50)
        assert fake.puts == []

    def test_an_unknown_name_reads_the_bridge_again_then_answers(self, integration, fake):
        integration.snapshot()
        reads = len(fake.gets)
        text = integration.turn_on("Garagelampa")
        assert "Garagelampa" in text and fake.puts == []
        assert len(fake.gets) > reads

    def test_a_lamp_renamed_in_the_app_is_found_on_the_re_read(self, integration, fake):
        integration.snapshot()
        fake.resources["light"][0]["metadata"]["name"] = "Köksspots"
        assert integration.turn_on("Köksspots") == ""
        assert fake.puts == [("light", "l-tak", {"on": {"on": True}})]

    def test_an_ambiguous_name_asks_which(self, integration):
        assert "say which" in integration.turn_off("All lights")

    @pytest.mark.parametrize("failure, expected", [
        (HueError("resource not found"), "resource not found"),
        (requests.Timeout(), "Request timed out"),
        (requests.ConnectionError("refused"), "Could not reach the Hue bridge at 192.0.2.10"),
    ])
    def test_the_bridges_failures_come_back_as_text(self, integration, fake, failure, expected):
        integration.snapshot()
        fake.failure = failure
        assert integration.turn_on("Taklampa") == expected

    def test_a_failure_while_resolving_is_text_too(self, integration, fake):
        fake.failure = requests.ConnectionError("refused")
        assert integration.turn_on("Taklampa").startswith("Could not reach")


@pytest.mark.unit
class TestHealth:
    def test_reads_the_bridge_resource(self, integration, fake):
        assert integration.check_health() is True
        assert fake.gets == ["bridge"]

    def test_raises_what_the_client_raises(self, integration, fake):
        fake.failure = HueError("unauthorized connection")
        with pytest.raises(HueError):
            integration.check_health()
