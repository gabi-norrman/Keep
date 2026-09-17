import asyncio
import base64
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from assistant.SystemNote import system_note

from common.ConfigManager import ConfigManager
from ...Integration import Integration, IntegrationState
from common.logging import info, error, trace
from .TfaCodeHandler import TfaCodeHandler


class RingCameraError(Exception):
    pass


class RingConfigurationError(RingCameraError):
    pass


class RingAuthenticationError(RingCameraError):
    pass


class _RingTfaHandler(TfaCodeHandler):
    """Blocks until a 2FA code is provided via iMessage auto-detection or TUI."""

    service_name = "Ring"
    detection_source = "iMessage auto-detection + TUI"

    def start(self):
        super().start()
        self._notify_2fa_needed()

    def _notification_message(self) -> str:
        return (
            "Ring 2FA required. Attempting automatic code retrieval from text message. "
            "Manual entry available in Floxy TUI console (Ring tab) if needed."
        )


USER_AGENT = "Floxy/1.0"


class RingIntegration(Integration):
    """Connects to the Ring API and fetches doorbell camera snapshots."""

    def __init__(self, config_manager: Optional[ConfigManager] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self._config_manager = config_manager or ConfigManager()
        self._config = self._load_config(config)
        self._validate_config()
        self._ring = None
        self._auth = None
        self._auth_error: Optional[str] = None
        self._tfa_handler = _RingTfaHandler()
        self._connect_lock = threading.Lock()
        self._token_path = Path(self._config.get("token_path", "/tmp/ring_floxy/token.cache"))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._auto_2fa_message: Optional[str] = None
        self._auto_2fa_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "Ring"

    @property
    def config_keys(self) -> List[str]:
        return ["ring"]

    def check_health(self) -> bool:
        self._ensure_connected()
        return len(self.get_doorbell_names()) > 0

    def needs_2fa(self) -> bool:
        return not self._token_path.exists()

    @property
    def tfa_handler(self) -> _RingTfaHandler:
        return self._tfa_handler

    def on_needs_2fa(self):
        if self._auto_2fa_thread and self._auto_2fa_thread.is_alive():
            return
        self._auto_2fa_thread = threading.Thread(target=self._attempt_auto_connect, name="RingAuto2FA", daemon=True)
        self._auto_2fa_thread.start()

    def _attempt_auto_connect(self):
        try:
            info("Ring: Proactively triggering connection to initiate 2FA flow")
            self._ensure_connected()
        except RingAuthenticationError as e:
            trace(f"Ring: Auto-2FA connection attempt: {e}")
        except Exception as e:
            error(f"Ring: Auto-2FA unexpected error: {e}")

    def submit_auto_2fa_code(self, code: str, received_at: Optional[float] = None):
        """Auto-submit a 2FA code detected from iMessage."""
        if not self._tfa_handler.is_waiting:
            info("Ring: ignoring auto-detected 2FA code — not awaiting 2FA (unrequested Ring login)")
            system_note(self._tfa_handler.service_name,
                        "⚠️ A Ring 2FA code arrived but Floxy did not request one. "
                        "This likely means someone signed in to the Ring account elsewhere. "
                        "Ignoring the code.")
            return
        waiting_since = self._tfa_handler.waiting_since
        if received_at is not None and waiting_since is not None and received_at < waiting_since:
            info(f"Ring: ignoring auto-detected 2FA code — it arrived {waiting_since - received_at:.0f}s "
                 "before this 2FA flow started, so it belongs to an earlier attempt")
            return
        info("Ring: auto-detected 2FA code from iMessage, submitting...")
        self._tfa_handler.submit_code(code)
        self._auto_2fa_message = "2FA code auto-detected from text message — submitted!"
        system_note(self._tfa_handler.service_name,
                    "Ring 2FA code auto-detected from iMessage and submitted automatically. ✅")

    @property
    def auto_2fa_message(self) -> Optional[str]:
        """Return and clear the one-shot auto-2FA status message for the TUI."""
        msg = self._auto_2fa_message
        self._auto_2fa_message = None
        return msg

    def _load_config(self, override_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if override_config is not None:
            return override_config
        try:
            return self._config_manager.get("ring")
        except KeyError as exc:
            raise RingConfigurationError("Missing 'ring' section in configuration.") from exc

    def _validate_config(self):
        if not self._config.get("username"):
            raise RingConfigurationError("Missing Ring configuration value: username")
        if not self._config.get("password"):
            raise RingConfigurationError("Missing Ring configuration value: password")

    def _token_updated(self, token: dict):
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(token))

    def _load_cached_token(self) -> Optional[dict]:
        if self._token_path.exists():
            try:
                return json.loads(self._token_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                error(f"Ring: Failed to load cached token: {e}")
        return None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _ensure_connected(self):
        with self._connect_lock:
            if self._ring is not None:
                return
            self._get_loop().run_until_complete(self._async_connect())

    async def _async_connect(self):
        from ring_doorbell import Auth, Ring

        cached_token = self._load_cached_token()

        try:
            if cached_token:
                info("Ring: Connecting with cached token...")
                self._auth = Auth(USER_AGENT, cached_token, self._token_updated)
            else:
                info(f"Ring: Connecting as {self._config['username']}...")
                self._auth = Auth(USER_AGENT, None, self._token_updated)
                self._tfa_handler.start()
                try:
                    await self._auth.async_fetch_token(
                        self._config["username"],
                        self._config["password"],
                    )
                except Exception:
                    otp = self._tfa_handler.get()
                    if not otp:
                        self.state = IntegrationState.NEEDS_2FA
                        raise RingAuthenticationError("No 2FA code provided within timeout")
                    await self._auth.async_fetch_token(
                        self._config["username"],
                        self._config["password"],
                        otp,
                    )

            self._ring = Ring(self._auth)
            await self._ring.async_create_session()
            await self._ring.async_update_data()
            self._auth_error = None
            info("Ring: Connection successful.")
        except Exception as e:
            self._auth_error = str(e)
            error(f"Ring: Failed to connect: {e}")
            raise RingAuthenticationError(f"Failed to connect to Ring: {e}") from e

    def get_auth_status(self) -> Dict[str, Any]:
        if self._auth_error:
            return {"status": "error", "message": self._auth_error}
        if self._ring is None:
            return {"status": "not_initialized", "message": "Not connected to Ring"}
        return {"status": "authenticated", "message": "Connected to Ring"}

    def get_doorbell_names(self) -> List[str]:
        try:
            self._ensure_connected()
            if self._ring:
                devices = self._ring.devices()
                return [d.name for d in devices.doorbells]
        except Exception as e:
            error(f"Failed to get Ring doorbell names: {e}")
        return []

    def _fetch_doorbell_snapshot(self, doorbell) -> list:
        cam_name = doorbell.name
        info(f"Ring: Requesting snapshot from {cam_name}...")

        snapshot = self._get_loop().run_until_complete(
            doorbell.async_get_snapshot(retries=10, delay=3)
        )

        if snapshot is None:
            error(f"Ring: Snapshot for {cam_name} returned None after retries")
            return [{"type": "text", "text": f"Camera '{cam_name}': failed to get snapshot."}]

        b64_data = base64.b64encode(snapshot).decode("utf-8")
        info(f"Ring: Got snapshot from {cam_name} ({len(snapshot)} bytes)")
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_data}},
            {"type": "text", "text": f"Camera: {cam_name}"},
        ]

    def fetch_snapshot(self, _params=None) -> list:
        """Fetch snapshots from all Ring doorbells. Returns Anthropic-format content blocks."""
        self._ensure_connected()
        if not self._ring:
            raise RingCameraError("Not connected to Ring")

        devices = self._ring.devices()
        doorbells = devices.doorbells
        if not doorbells:
            return []

        content_blocks = []
        for doorbell in doorbells:
            try:
                blocks = self._fetch_doorbell_snapshot(doorbell)
                content_blocks.extend(blocks)
            except Exception as e:
                error(f"Ring: Failed to get snapshot from {doorbell.name}: {e}")
                content_blocks.append({"type": "text", "text": f"Camera '{doorbell.name}': error - {e}"})

        return content_blocks

    def refresh_connection(self):
        with self._connect_lock:
            old_auth = self._auth
            old_loop = self._loop

            self._ring = None
            self._auth = None
            self._auth_error = None
            self._loop = None

            if old_auth:
                try:
                    cleanup_loop = asyncio.new_event_loop()
                    cleanup_loop.run_until_complete(old_auth.async_close())
                    cleanup_loop.close()
                except Exception:
                    pass

            if old_loop and not old_loop.is_closed():
                try:
                    old_loop.close()
                except Exception:
                    pass

            self._get_loop().run_until_complete(self._async_connect())

