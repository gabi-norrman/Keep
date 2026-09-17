import base64
import hashlib
import os
import re
import ssl
import threading
import time
import urllib3
from typing import Any, Dict, List, Optional
from assistant.SystemNote import system_note

import cloudscraper
import pyaarlo
import pyaarlo.constant
from requests.adapters import HTTPAdapter

from common.ConfigManager import ConfigManager
from ...Integration import Integration, IntegrationState
from common.logging import info, error, note, trace
from .jpeg_freshness import looks_like_cached_thumbnail
from .TfaCodeHandler import TfaCodeHandler

STALE_RETRY_DELAY_SECONDS = 10
SNAPSHOT_TIMEOUT_SECONDS = 30


class ArloCameraError(Exception):
    pass


class ArloConfigurationError(ArloCameraError):
    pass


class ArloAuthenticationError(ArloCameraError):
    pass


ARLO_2FA_PATTERN = re.compile(r'\b(\d{6})\b')
ARLO_SENDER_FILTER = "arlo"
EMAIL_POLL_INTERVAL = 5
EMAIL_POLL_MAX_ATTEMPTS = 24
EMAIL_SINCE_MINUTES = 5


class _ArloTfaHandler(TfaCodeHandler):
    """pyaarlo TFA handler — adds email auto-detection on top of the shared base."""

    service_name = "Arlo"
    detection_source = "email auto-detection (Gmail / iCloud Email) + TUI"

    def __init__(self):
        super().__init__()
        self._notified = False
        self._stop_email_polling = False

    def start(self):
        super().start()
        self._stop_email_polling = True
        return True

    def reset(self):
        self._notified = False

    def get(self, timeout: float = 300) -> Optional[str]:
        if not self._notified:
            self._notified = True
            self._notify_2fa_needed()
            self._start_email_polling()
        code = super().get(timeout=timeout)
        self._stop_email_polling = True
        return code

    def stop(self):
        self._stop_email_polling = True

    def _notification_message(self) -> str:
        return (
            "Arlo 2FA required. Attempting automatic code retrieval from email. "
            "Manual entry available in Keep TUI console (Arlo tab) if needed."
        )

    def _start_email_polling(self):
        self._stop_email_polling = False
        thread = threading.Thread(target=self._poll_email_for_code, name="ArloEmailPoll", daemon=True)
        thread.start()

    def _poll_email_for_code(self):
        try:
            providers = self._email_providers()
            if not providers:
                info("Arlo: No email account configured, skipping email auto-detection")
                return
            info(f"Arlo: Polling {', '.join(str(p.name) for p in providers)} for the 2FA code")

            for attempt in range(EMAIL_POLL_MAX_ATTEMPTS):
                for provider in providers:
                    if self._stop_email_polling or self._event.is_set():
                        return
                    code = self._try_extract_code_from_email(provider)
                    if code:
                        info(f"Arlo: Auto-detected 2FA code from {provider.name}")
                        self.submit_code(code)
                        system_note(self.service_name,
                                    f"Arlo 2FA code auto-detected from {provider.name} and submitted automatically. ✅")
                        return

                time.sleep(EMAIL_POLL_INTERVAL)

            info("Arlo: Email polling timed out, waiting for manual TUI entry")
            if not self._event.is_set():
                system_note(self.service_name,
                            "Arlo 2FA code not found in email after "
                            f"{EMAIL_POLL_MAX_ATTEMPTS * EMAIL_POLL_INTERVAL // 60} minutes of polling. "
                            "Enter it manually in the Keep TUI console (Arlo tab).")
        except Exception as e:
            error(f"Arlo: Email polling error: {e}")

    @staticmethod
    def _email_providers() -> list:
        """Every configured email account (Gmail, iCloud Email), like the mail commands
        see them; the Arlo account's address could be on either."""
        providers = []
        try:
            config = ConfigManager.get_instance()
            if config.get("email.address", ""):
                from ..email.GmailIntegration import GmailIntegration
                providers.append(GmailIntegration.get_instance())
            if config.get("icloud_email.address", ""):
                from ..email.ICloudEmailIntegration import ICloudEmailIntegration
                providers.append(ICloudEmailIntegration.get_instance())
        except Exception as e:
            error(f"Arlo: Could not set up the email providers for 2FA polling: {e}")
        return providers

    @staticmethod
    def _try_extract_code_from_email(provider) -> Optional[str]:
        messages = provider.fetch_recent_raw(
            sender_contains=ARLO_SENDER_FILTER,
            since_minutes=EMAIL_SINCE_MINUTES,
            limit=3
        )

        for msg in messages:
            body = msg.text or ""
            if not body and msg.html:
                # Plain-text flatten (no href URLs): a 6-digit run inside a link
                # URL would otherwise be matched ahead of the real 2FA code.
                body = provider.strip_html_plain(msg.html)

            match = ARLO_2FA_PATTERN.search(body)
            if match:
                trace(f"Arlo: Found 6-digit code in email from {msg.from_} (subject: {msg.subject})")
                return match.group(1)

        return None


class ArloCameraIntegration(Integration):
    """Connects to the Arlo cloud via PyArlo and fetches camera snapshots.

    Failed logins back off exponentially (persisted across restarts) and lock out for 24h
    after MAX_CONSECUTIVE_FAILURES, so a broken 2FA flow can never hammer Arlo's auth.
    """

    REAL_CHECK_EVERY = 4  # do a real health check every Nth call (4 × 15 min = 1 hour)
    BASE_AUTH_COOLDOWN = 10 * 60  # 10 minutes base cooldown
    BACKOFF_FACTOR = 3
    MAX_AUTH_COOLDOWN = 4 * 3600  # 4 hours max cooldown
    MAX_CONSECUTIVE_FAILURES = 10  # after this, 24h lockout
    LOCKOUT_COOLDOWN = 24 * 3600  # 24 hours

    def __init__(self, config_manager: Optional[ConfigManager] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self._config_manager = config_manager or ConfigManager()
        self._arlo: Optional[pyaarlo.PyArlo] = None
        self._auth_error: Optional[str] = None
        self._tfa_handler = _ArloTfaHandler()
        self._health_check_counter: int = 0
        self._connect_lock = threading.Lock()
        self._consecutive_auth_failures: int = 0
        self._last_auth_attempt_time: float = 0.0
        self._auto_2fa_thread: Optional[threading.Thread] = None
        self._login_error: Optional[str] = None   # the backend's reason when Arlo rejected the credentials

        try:
            self._config = self._load_config(config)
            self._validate_config()
        except ArloConfigurationError:
            self._config = config or {}
            self._ignore_keywords: List[str] = []
            return

        self._ignore_keywords = [k.lower() for k in self._config.get("ignore_cameras", [])]
        self._load_persisted_state()

    def _load_persisted_state(self):
        """Load the cooldown timestamp, failure count, credentials fingerprint and the last
        login rejection from persistent storage. State recorded under other credentials is
        discarded: a password just re-entered deserves an immediate attempt, not the old
        backoff."""
        try:
            with open(self._cooldown_file) as f:
                lines = f.read().split('\n')
            self._last_auth_attempt_time = float(lines[0])
            self._consecutive_auth_failures = int(lines[1]) if len(lines) > 1 and lines[1] else 0
            fingerprint = lines[2] if len(lines) > 2 else ""
            self._login_error = lines[3] if len(lines) > 3 and lines[3] else None
        except (FileNotFoundError, ValueError, IndexError):
            self._last_auth_attempt_time = 0.0
            self._consecutive_auth_failures = 0
            self._login_error = None
            return
        if fingerprint and fingerprint != self._credentials_fingerprint():
            info("Arlo: credentials changed since the last attempt — cooldown and login error cleared")
            self._last_auth_attempt_time = 0.0
            self._consecutive_auth_failures = 0
            self._login_error = None
            self._save_persisted_state()

    def _save_persisted_state(self):
        """Save the cooldown timestamp, failure count, credentials fingerprint and the last
        login rejection to persistent storage."""
        try:
            os.makedirs(os.path.dirname(self._cooldown_file), exist_ok=True)
            with open(self._cooldown_file, "w") as f:
                f.write(f"{self._last_auth_attempt_time}\n{self._consecutive_auth_failures}\n"
                        f"{self._credentials_fingerprint()}\n{self._login_error or ''}")
        except OSError:
            pass

    def _credentials_fingerprint(self) -> str:
        raw = f"{self._config.get('username', '')}\n{self._config.get('password', '')}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def name(self) -> str:
        return "Arlo"

    @property
    def config_keys(self) -> List[str]:
        return ["arlo"]

    def check_health(self) -> bool:
        self._health_check_counter += 1
        if self._health_check_counter % self.REAL_CHECK_EVERY != 1 and self.state == IntegrationState.OK:
            return True
        # Connect here rather than through get_camera_names, which swallows the error: the
        # monitor records the message, and "auth cooldown; last attempt: 401" tells the user
        # what to fix where "health check returned False" would not.
        self._ensure_connected()
        return len(self.get_camera_names()) > 0

    def needs_2fa(self) -> bool:
        """No saved session yet — unless Arlo rejected the credentials, which no code fixes."""
        from pathlib import Path
        if self._login_error:
            return False
        storage_dir = self._config.get("storage_dir", "/tmp/pyaarlo_floxy")
        return not Path(storage_dir, "session.pickle").exists()

    def on_needs_2fa(self):
        if self._auto_2fa_thread and self._auto_2fa_thread.is_alive():
            return
        self._auto_2fa_thread = threading.Thread(target=self._attempt_auto_connect, name="ArloAuto2FA", daemon=True)
        self._auto_2fa_thread.start()

    def _attempt_auto_connect(self):
        try:
            self._ensure_connected()
        except ArloAuthenticationError as e:
            trace(f"Arlo: Auto-2FA connection attempt: {e}")
        except Exception as e:
            error(f"Arlo: Auto-2FA unexpected error: {e}")

    def on_not_configured(self):
        from pathlib import Path
        storage_dir = self._config.get("storage_dir", "/tmp/pyaarlo_floxy")
        cleared = self._clear_stale_session(storage_dir)
        cooldown_path = Path(storage_dir) / "auth_cooldown.txt"
        if cooldown_path.exists():
            cooldown_path.unlink()
            cleared = True
        if cleared:
            info("Arlo: Cleared persisted state (integration not configured)")

    @property
    def tfa_handler(self) -> _ArloTfaHandler:
        return self._tfa_handler

    def _load_config(self, override_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if override_config is not None:
            return override_config
        try:
            return self._config_manager.get("arlo")
        except KeyError as exc:
            raise ArloConfigurationError("Missing 'arlo' section in configuration.") from exc

    def _validate_config(self):
        if not self._config.get("username"):
            raise ArloConfigurationError("Missing Arlo configuration value: username")
        if not self._config.get("password"):
            raise ArloConfigurationError("Missing Arlo configuration value: password")

    @property
    def _cooldown_file(self) -> str:
        from pathlib import Path
        storage_dir = self._config.get("storage_dir", "/tmp/pyaarlo_floxy")
        return str(Path(storage_dir) / "auth_cooldown.txt")

    def _patch_ssl_and_cloudscraper(self):
        """Apply SSL and cloudscraper patches needed for pyaarlo compatibility."""
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        class _NoVerifyHTTPAdapter(HTTPAdapter):
            def init_poolmanager(self, *args, **kwargs):
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                kwargs['ssl_context'] = ctx
                return super().init_poolmanager(*args, **kwargs)

        original_create_scraper = cloudscraper.create_scraper

        def patched_create_scraper(*args, **kwargs):
            if 'browser' not in kwargs:
                kwargs['browser'] = {
                    'browser': 'chrome',
                    'platform': 'darwin',
                    'desktop': True,
                    'mobile': False
                }
            session = original_create_scraper(*args, **kwargs)
            session.verify = False
            adapter = _NoVerifyHTTPAdapter()
            session.mount('https://', adapter)
            session.mount('http://', adapter)
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            return session

        cloudscraper.create_scraper = patched_create_scraper

    def _create_pyarlo(self, storage_dir: str) -> pyaarlo.PyArlo:
        # Force single ECDH curve to avoid pyaarlo's CAN_RETRY loop that doubles API calls
        pyaarlo.constant.ECDH_CURVES[:] = ['prime256v1']
        return pyaarlo.PyArlo(
            username=self._config["username"],
            password=self._config["password"],
            tfa_source=self._tfa_handler,
            save_session=True,
            storage_dir=storage_dir,
            mqtt_hostname_check=False,
            backend='sse',
        )

    @staticmethod
    def _clear_stale_session(storage_dir: str) -> bool:
        """Remove saved session files. Returns True if any were removed."""
        from pathlib import Path
        cleared = False
        for name in ("session.pickle", "cookies.txt"):
            p = Path(storage_dir) / name
            if p.exists():
                p.unlink()
                cleared = True
        return cleared

    @property
    def _current_cooldown(self) -> float:
        """Calculate cooldown duration based on consecutive auth failures (exponential backoff)."""
        if self._consecutive_auth_failures == 0:
            return 0
        if self._consecutive_auth_failures >= self.MAX_CONSECUTIVE_FAILURES:
            return self.LOCKOUT_COOLDOWN
        return min(
            self.BASE_AUTH_COOLDOWN * (self.BACKOFF_FACTOR ** (self._consecutive_auth_failures - 1)),
            self.MAX_AUTH_COOLDOWN
        )

    @property
    def auth_cooldown_remaining(self) -> float:
        """Seconds remaining before the next login attempt is allowed (0 if ready)."""
        elapsed = time.time() - self._last_auth_attempt_time
        return max(0.0, self._current_cooldown - elapsed)

    @staticmethod
    def _is_rate_limit_error(error_str: str) -> bool:
        """Check if an error indicates Arlo API rate limiting (429 or 400 from auth)."""
        if not error_str:
            return False
        return bool(re.search(r'\b429\b', error_str) or re.search(r'\b400\b', error_str))

    @staticmethod
    def _is_credential_error(error_str: str) -> bool:
        """Arlo rejected the username/password (401): the login never reached the 2FA step."""
        if not error_str:
            return False
        return bool(re.search(r'\b401\b', error_str)) or "password not correct" in error_str.lower()

    def _ensure_connected(self):
        with self._connect_lock:
            self._do_connect()

    def _do_connect(self):
        if self._arlo is not None:
            return
        # An instance built before the arlo section existed has no credentials to try with;
        # that is a configuration error, not an auth failure to count and back off from.
        self._validate_config()

        if self._consecutive_auth_failures >= self.MAX_CONSECUTIVE_FAILURES:
            cooldown = self.auth_cooldown_remaining
            if cooldown > 0:
                hours, remainder = divmod(int(cooldown), 3600)
                mins, _ = divmod(remainder, 60)
                note(f"Arlo: Locked out after {self._consecutive_auth_failures} failures — retry in {hours}h {mins}m (manual reset or wait)")
                raise ArloAuthenticationError(f"Locked out after {self._consecutive_auth_failures} consecutive failures — retry in {hours}h {mins}m")

        cooldown = self.auth_cooldown_remaining
        if cooldown > 0:
            mins, secs = divmod(int(cooldown), 60)
            reason = f"; last attempt: {self._login_error}" if self._login_error else ""
            note(f"Arlo: Auth cooldown active — next attempt allowed in {mins}m {secs}s (attempt {self._consecutive_auth_failures}){reason}")
            raise ArloAuthenticationError(f"Auth cooldown — retry in {mins}m {secs}s{reason}")

        self._patch_ssl_and_cloudscraper()

        storage_dir = self._config.get("storage_dir", "/tmp/pyaarlo_floxy")
        os.makedirs(storage_dir, exist_ok=True)

        try:
            if self._consecutive_auth_failures == 0:
                info(f"Arlo: Connecting as {self._config['username']}...")
            else:
                info(f"Arlo: Connecting as {self._config['username']} (attempt {self._consecutive_auth_failures + 1})...")
            self._last_auth_attempt_time = time.time()
            self._tfa_handler.reset()
            self._arlo = self._create_pyarlo(storage_dir)

            if not self._arlo._be.is_connected:
                last_error = getattr(self._arlo, '_last_error', None)
                self._arlo = None

                if self._clear_stale_session(storage_dir):
                    note("Arlo: Cleared stale session — will start fresh on next attempt.")

                self._consecutive_auth_failures += 1
                self._save_persisted_state()

                last_error_str = str(last_error) if last_error else ""
                if self._is_rate_limit_error(last_error_str):
                    self.state = IntegrationState.RATE_LIMITED
                    next_cooldown = self._current_cooldown
                    mins, _ = divmod(int(next_cooldown), 60)
                    error(f"Arlo: Rate limited (HTTP 400/429). Next retry in {mins}m. (failure #{self._consecutive_auth_failures})")
                elif self._is_credential_error(last_error_str):
                    # Never reached the 2FA step: reporting NEEDS_2FA would send the user
                    # to wait for a code that is not coming.
                    self._login_error = last_error_str
                    self._save_persisted_state()
                    self.state = IntegrationState.ERROR
                    self._last_error = f"Arlo rejected the login ({last_error_str}) — check arlo.username and arlo.password"
                    error(f"Arlo: {self._last_error}")
                else:
                    self.state = IntegrationState.NEEDS_2FA
                    error(f"Arlo: Backend not connected after init. last_error={last_error}")
                raise ArloAuthenticationError(f"Arlo backend failed to connect (last_error={last_error})")

            # Success — reset failure counter
            self._consecutive_auth_failures = 0
            self._login_error = None
            self._save_persisted_state()
            self.state = IntegrationState.OK
            self._auth_error = None
            devices = getattr(self._arlo, '_devices', [])
            info(f"Arlo: Connected ({len(devices)} devices, {len(self._arlo.cameras)} cameras, {[c.name for c in self._arlo.cameras]})")
        except ArloAuthenticationError as e:
            self._auth_error = str(e)
            raise
        except Exception as e:
            self._consecutive_auth_failures += 1
            self._save_persisted_state()
            self._auth_error = str(e)
            error_str = str(e)
            if self._is_rate_limit_error(error_str):
                self.state = IntegrationState.RATE_LIMITED
            error(f"Arlo: Failed to connect: {e}")
            raise ArloAuthenticationError(f"Failed to connect to Arlo: {e}") from e

    def get_auth_status(self) -> Dict[str, Any]:
        if self._auth_error:
            return {"status": "error", "message": self._auth_error}
        if self._arlo is None:
            return {"status": "not_initialized", "message": "Not connected to Arlo"}
        return {"status": "authenticated", "message": "Connected to Arlo"}

    def _is_ignored(self, camera_name: str) -> bool:
        name_lower = camera_name.lower()
        return any(kw in name_lower for kw in self._ignore_keywords)

    def get_camera_names(self) -> List[str]:
        try:
            self._ensure_connected()
            if self._arlo:
                return [cam.name for cam in self._arlo.cameras if not self._is_ignored(cam.name)]
        except Exception as e:
            error(f"Failed to get Arlo camera names: {e}")
        return []

    @staticmethod
    def _fetch_single_snapshot(cam) -> list:
        cam_name = cam.name
        info(f"Arlo: Requesting snapshot from {cam_name}...")
        try:
            previous_image = cam.last_image_from_cache
            snapshot = cam.get_snapshot(timeout=SNAPSHOT_TIMEOUT_SECONDS)
            if snapshot is None:
                return [{"type": "text", "text": f"Camera '{cam_name}': failed to get snapshot (timeout)."}]

            # pyaarlo's get_snapshot returns last_image_from_cache on timeout, so an
            # unchanged cache means the fresh-capture request timed out — secondary signal
            # that complements the resolution/encoder thumbnail heuristic below.
            if previous_image is not None and snapshot == previous_image:
                note(f"Arlo: {cam_name} snapshot bytes unchanged from cache — likely timed-out get_snapshot")

            is_stale, reason = looks_like_cached_thumbnail(snapshot)
            if is_stale:
                info(f"Arlo: {cam_name} returned cached thumbnail ({reason}); waking and retrying once")
                time.sleep(STALE_RETRY_DELAY_SECONDS)
                retry = cam.get_snapshot(timeout=SNAPSHOT_TIMEOUT_SECONDS)
                if retry is not None:
                    retry_stale, retry_reason = looks_like_cached_thumbnail(retry)
                    if not retry_stale:
                        info(f"Arlo: {cam_name} retry returned fresh snapshot ({len(retry)} bytes)")
                        snapshot = retry
                        is_stale = False
                    else:
                        error(f"Arlo: {cam_name} retry still stale ({retry_reason}); surfacing warning to LLM")
                        snapshot = retry
                        reason = retry_reason
                else:
                    error(f"Arlo: {cam_name} retry returned None; keeping first stale image")

            b64_data = base64.b64encode(snapshot).decode("utf-8")
            label = f"Camera: {cam_name}"
            if is_stale:
                label += f" ⚠️ image is STALE ({reason}), do NOT trust it - it is an old cached frame. If you reference it, TELL the user the image is stale."
            info(f"Arlo: Got snapshot from {cam_name} ({len(snapshot)} bytes, stale={is_stale})")
            return [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_data}},
                {"type": "text", "text": label},
            ]
        except Exception as e:
            error(f"Arlo: Failed to get snapshot from {cam_name}: {e}")
            return [{"type": "text", "text": f"Camera '{cam_name}': error - {e}"}]

    def fetch_all_snapshots(self, _params=None) -> list:
        """Fetch snapshots from all cameras in parallel. Returns Anthropic-format content blocks."""
        from concurrent.futures import ThreadPoolExecutor

        self._ensure_connected()
        if not self._arlo:
            raise ArloCameraError("Not connected to Arlo")

        all_cameras = self._arlo.cameras
        info(f"Arlo: Found {len(all_cameras)} cameras: {[c.name for c in all_cameras]}")
        cameras = [cam for cam in all_cameras if not self._is_ignored(cam.name)]
        if not cameras:
            note("Arlo: No cameras available after filtering")
            return [{"type": "text", "text": "No Arlo cameras found."}]

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(self._fetch_single_snapshot, cameras))

        content_blocks = []
        for blocks in results:
            content_blocks.extend(blocks)

        if not content_blocks:
            return [{"type": "text", "text": "Failed to get snapshots from any camera."}]

        content_blocks.insert(0, {"type": "text", "text": "Here are the current camera images from around the house:"})
        return content_blocks

    def refresh_connection(self):
        with self._connect_lock:
            self._arlo = None
            self._auth_error = None
            self._login_error = None
            self._consecutive_auth_failures = 0
            self._save_persisted_state()
            self._do_connect()


ArloCamera = ArloCameraIntegration
