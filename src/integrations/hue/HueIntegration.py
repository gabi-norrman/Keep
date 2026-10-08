"""Philips Hue as the home automation backend, read whole on every snapshot and written with
one PUT per command. A snapshot is one GET per resource type over the LAN, mapped by HueModel;
a command resolves its name through the index of the last read, and a name the index does not
know makes it read the bridge once more before answering, so a lamp renamed in the Hue app is
found at once. The health check reads the bridge resource; the application key the client
sends comes from `hue_pair` (HueCommands)."""
import threading
import time
from typing import List, Optional

import requests

from .HueClient import HueClient, HueError
from .HueModel import LIGHT_TYPES, RESOURCE_TYPES, NameIndex, Target, Unresolved, build
from assistant.integration.HomeAutomation import HomeAutomation, HomeSnapshot
from assistant.integration.Integration import Integration
from common.ConfigManager import config
from common.logging import info, trace


class HueIntegration(Integration, HomeAutomation):
    """The lights, rooms and scenes of a Philips Hue bridge, plus its sensors.

    Makes a Hue bridge a home automation backend: Keep sees every light and room with its
    state, switches and dims them, recalls the scenes, and reads the motion sensors'
    temperature, motion and light level.

    Talks to the bridge directly over its CLIP v2 API; hue_pair earns the application key."""

    def __init__(self):
        super().__init__()
        self._client: Optional[HueClient] = None
        self._lock = threading.Lock()
        self._index: Optional[NameIndex] = None

    def _on_reset(self):
        self._client = None

    # --- Integration ------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Philips Hue"

    @property
    def config_keys(self) -> List[str]:
        return ["hue.bridge", "hue.app_key"]

    @property
    def config_section(self) -> str:
        return "hue"

    def client(self) -> HueClient:
        """The client, built from config on first use."""
        if self._client is None:
            self._client = HueClient(str(config.get("hue.bridge", "") or ""), str(config.get("hue.app_key", "") or ""))
        return self._client

    def check_health(self) -> bool:
        bridges = self.client().get("bridge")
        trace(f"Philips Hue: bridge {bridges[0].get('bridge_id', '?') if bridges else '?'} answered")
        return True

    # --- HomeAutomation: state ------------------------------------------------------------------

    def snapshot(self) -> HomeSnapshot:
        snapshot, _ = self._read()
        return snapshot

    def _read(self) -> tuple[HomeSnapshot, NameIndex]:
        client = self.client()
        resources = {rtype: client.get(rtype) for rtype in RESOURCE_TYPES}
        snapshot, index = build(resources)
        snapshot.taken_at = time.time()
        with self._lock:
            self._index = index
        trace(f"Philips Hue: {len(snapshot.lights)} lights, {len(snapshot.actions)} scenes, "
              f"{len(snapshot.sensors)} sensor readings")
        return snapshot, index

    # --- HomeAutomation: commands ---------------------------------------------------------------

    def turn_on(self, name: str) -> str:
        return self._switch(name, True)

    def turn_off(self, name: str) -> str:
        return self._switch(name, False)

    def dim(self, name: str, level: int) -> str:
        level = int(level)
        if level <= 0:
            return self.turn_off(name)
        target, text = self._resolve(name)
        if target is None:
            return text
        if target.rtype not in LIGHT_TYPES:
            return self._wrong_command(target, "dim")
        return self._put(target, {"on": {"on": True}, "dimming": {"brightness": min(level, 100)}})

    def execute(self, name: str) -> str:
        target, text = self._resolve(name)
        if target is None:
            return text
        if target.rtype != "scene":
            return self._wrong_command(target, "execute")
        return self._put(target, {"recall": {"action": "active"}})

    def _switch(self, name: str, on: bool) -> str:
        target, text = self._resolve(name)
        if target is None:
            return text
        if target.rtype not in LIGHT_TYPES:
            return self._wrong_command(target, "turn_on" if on else "turn_off")
        return self._put(target, {"on": {"on": on}})

    @staticmethod
    def _wrong_command(target: Target, verb: str) -> str:
        if target.rtype == "scene":
            return f"{target.display} is a scene; use execute."
        if target.rtype in LIGHT_TYPES:
            return f"{target.display} is a light; use turn_on / turn_off / dim."
        return f"{target.display} is a sensor reading and cannot be controlled with {verb}."

    def _resolve(self, name: str) -> tuple[Optional[Target], str]:
        """(the target, "") or (None, the text for the LLM). A name the index does not hold
        reads the bridge again first: the index may predate a rename in the Hue app."""
        try:
            with self._lock:
                index = self._index
            if index is None:
                _, index = self._read()
            try:
                return index.resolve(name), ""
            except Unresolved:
                info(f"Philips Hue: '{name}' is not in the index — reading the bridge again")
                _, index = self._read()
                return index.resolve(name), ""
        except Unresolved as e:
            info(f"Philips Hue: {e}")
            return None, str(e)
        except (HueError, requests.RequestException) as e:
            info(f"Philips Hue: cannot resolve '{name}': {e}")
            return None, self._failure(e)

    def _put(self, target: Target, body: dict) -> str:
        try:
            self.client().put(target.rtype, target.rid, body)
        except (HueError, requests.RequestException) as e:
            info(f"Philips Hue: PUT {target.rtype}/{target.rid} {body} failed: {e}")
            return self._failure(e)
        info(f"Philips Hue: {target.display} ← {body}")
        return ""

    def _failure(self, e: Exception) -> str:
        if isinstance(e, HueError):
            return str(e)
        if isinstance(e, requests.Timeout):
            return "Request timed out"
        return f"Could not reach the Hue bridge at {self.client().host}"
