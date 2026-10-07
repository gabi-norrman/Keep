"""UniFi Protect camera snapshots over the console's Integration API."""
import base64
from typing import Any, Dict, List, Optional

import requests
import urllib3

from assistant.integration.CameraProvider import CameraProvider, select
from assistant.integration.Integration import Integration
from assistant.tools.ImageAnalyzer import resize_to_jpeg
from common.ConfigManager import ConfigManager
from common.logging import info, error, trace


class UnifiProtectError(Exception):
    pass


class UnifiProtectConfigurationError(UnifiProtectError):
    pass


API_PATH = "/proxy/protect/integration/v1"
TIMEOUT_SECONDS = 15
# Anthropic downscales anything wider than this anyway; a full-quality Protect frame is over 1 MB.
MAX_DIMENSION = 1568


class UnifiProtectIntegration(Integration, CameraProvider):
    """UniFi Protect camera snapshots.

    Fetches a fresh still from every camera on a UniFi Protect console on the local network, so
    Keep can say what the cameras see. Setup is the console's address and an API key created in
    its settings (Control Plane > Integrations) — no login and no 2FA.

    Talks to the console's Integration API (/proxy/protect/integration/v1) with the X-API-KEY
    header. The console's certificate is self-signed, so verification is off unless verify_ssl
    says otherwise. A full-quality snapshot is resized to MAX_DIMENSION before it goes to the
    LLM; a camera the console reports as not connected is answered without a request."""
    sensitive = True

    def __init__(self, config_manager: Optional[ConfigManager] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self._config_manager = config_manager or ConfigManager()
        self._config = self._load_config(config)
        self._validate_config()
        self._base_url = self._normalise_host(self._config["host"]) + API_PATH
        self._session = self._new_session()

    @property
    def name(self) -> str:
        return "UniFi Protect"

    @property
    def config_keys(self) -> List[str]:
        return ["unifi_protect.host", "unifi_protect.api_key"]

    def check_health(self) -> bool:
        return len(self.camera_names()) > 0

    def _load_config(self, override_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if override_config is not None:
            return override_config
        try:
            return self._config_manager.get("unifi_protect")
        except KeyError as exc:
            raise UnifiProtectConfigurationError("Missing 'unifi_protect' section in configuration.") from exc

    def _validate_config(self):
        for key in ("host", "api_key"):
            if not self._config.get(key):
                raise UnifiProtectConfigurationError(f"Missing UniFi Protect configuration value: {key}")

    @staticmethod
    def _normalise_host(host: str) -> str:
        host = str(host).strip().rstrip("/")
        return host if host.startswith(("http://", "https://")) else f"https://{host}"

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"X-API-KEY": self._config["api_key"], "Accept": "application/json"})
        session.verify = bool(self._config.get("verify_ssl", False))
        if not session.verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return session

    def _cameras(self) -> List[dict]:
        """The console's camera list: id, name and state per camera. Raises when it cannot be reached."""
        try:
            response = self._session.get(f"{self._base_url}/cameras", timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            cameras = response.json()
        except Exception as e:
            raise UnifiProtectError(f"Cannot reach the UniFi Protect console: {e}") from e
        trace(f"UniFi Protect: console lists {len(cameras)} camera(s)")
        return cameras

    def _select(self, cameras: List[dict], only: Optional[List[str]] = None) -> List[dict]:
        chosen = select([c["name"] for c in cameras], self._config.get("ignore_cameras", []), only)
        return [c for c in cameras if c["name"] in chosen]

    def camera_names(self) -> List[str]:
        try:
            return [c["name"] for c in self._select(self._cameras())]
        except Exception as e:
            error(f"UniFi Protect: failed to list the cameras: {e}")
            return []

    def _fetch_snapshot(self, camera: dict) -> list:
        cam_name = camera["name"]
        state = camera.get("state", "CONNECTED")
        if state != "CONNECTED":
            info(f"UniFi Protect: {cam_name} is {state}, no snapshot requested")
            return [{"type": "text", "text": f"Camera '{cam_name}': offline ({state})."}]
        info(f"UniFi Protect: requesting snapshot from {cam_name}...")
        response = self._session.get(f"{self._base_url}/cameras/{camera['id']}/snapshot",
                                     params={"highQuality": "true"}, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        raw = response.content
        snapshot = resize_to_jpeg(raw, max_dim=MAX_DIMENSION)
        if snapshot is None:
            error(f"UniFi Protect: resizing the {cam_name} snapshot failed, sending the original")
            snapshot = raw
        info(f"UniFi Protect: got snapshot from {cam_name} ({len(raw)} bytes, {len(snapshot)} after resize)")
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(snapshot).decode("utf-8")}},
            {"type": "text", "text": f"Camera: {cam_name}"},
        ]

    def fetch_snapshots(self, only: Optional[List[str]] = None) -> list:
        """A still from every camera (or the ones *only* names), as content blocks."""
        cameras = self._select(self._cameras(), only)
        if not cameras:
            trace(f"UniFi Protect: no cameras selected (asked for {only})")
            return []
        content_blocks = []
        for camera in cameras:
            try:
                content_blocks.extend(self._fetch_snapshot(camera))
            except Exception as e:
                error(f"UniFi Protect: failed to get snapshot from {camera['name']}: {e}")
                content_blocks.append({"type": "text", "text": f"Camera '{camera['name']}': error - {e}"})
        return content_blocks
