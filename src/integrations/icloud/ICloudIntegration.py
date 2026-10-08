import logging
import pathlib
import shutil
import threading
import time
import types
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from assistant.SystemNote import system_note

from pyicloud import PyiCloudService
from pyicloud.base import resolve_cookie_directory
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudAPIResponseException

from common.ConfigManager import ConfigManager
from common.Paths import ICLOUD_SESSION_DIR
from assistant.tools.Geocoding import reverse_geocode
from common.RateLimiter import RateLimiter
from assistant.integration.Integration import Integration, IntegrationState
from common.logging import info, error, note, trace, fine

_EXC_LOG_LIMIT = 300


def _brief(e: BaseException, limit: int = _EXC_LOG_LIMIT) -> str:
    """An exception rendered for one log line. pyicloud appends the entire HTTP body
    to its API errors — on 2026-09-06 that put Apple's 940-line 2FA page into
    floxy.out — so collapse the whitespace and cut the tail, keeping the reason
    and status code that lead the message."""
    text = " ".join(str(e).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [{len(text)} chars, {type(e).__name__}]"


def _request_sms_code_only(api: PyiCloudService) -> None:
    """Replacement for PyiCloudService._request_2fa_code (2.6.x): request the SMS
    code for the pending challenge without the trusted-device push."""
    from pyicloud.const import CONTENT_TYPE_JSON
    phone = api._trusted_phone_number()
    if phone is None:
        trace("iCloud: signin challenge has no trusted phone — not requesting an SMS code")
        return
    try:
        api.session.put(
            f"{api._auth_endpoint}/verify/phone",
            json={"phoneNumber": phone.as_phone_number_payload(), "mode": "sms"},
            headers=api._get_auth_headers({"Accept": CONTENT_TYPE_JSON}),
        )
        trace(f"iCloud: requested SMS 2FA code for phone id {phone.device_id} (no device push)")
    except Exception as e:
        info(f"iCloud: SMS code request after signin failed ({_brief(e)}) — Apple usually pushes one anyway")


_MFA_PAGE_STATUSES = (200, 409)


def _get_mfa_auth_options_tolerant(api: PyiCloudService) -> Dict[str, Any]:
    """Replacement for PyiCloudService._get_mfa_auth_options (2.6.x): read the 2FA
    page Apple serves after a password signin whether it arrives with 200 or 409.
    Apple answers 409 for 'signin incomplete, second factor pending' on its other
    auth endpoints too, and on 2026-09-06 it served this page that way — complete,
    boot args included. pyicloud's normal request path turns any 409 into
    'Authentication required for Account.' and aborts the signin before the SMS
    step, so fetch the page raw and parse whatever came back."""
    from pyicloud.hsa2_bridge import Hsa2BootContext, parse_boot_args_html
    headers = api._get_auth_headers({"Accept": "text/html"})
    response = api.session.request_raw("GET", api._auth_endpoint, headers=headers)
    status = response.status_code
    if status not in _MFA_PAGE_STATUSES:
        raise PyiCloudAPIResponseException(
            response.reason or "2FA page request failed", status, response)

    try:
        response_json = response.json()
    except (AttributeError, TypeError, ValueError):
        response_json = None

    auth_options: Dict[str, Any] = {}
    if isinstance(response_json, dict):
        auth_options.update(response_json)
        boot_context = Hsa2BootContext.from_auth_options(auth_options)
    else:
        boot_context = parse_boot_args_html(response.text or "")
    if status != 200:
        info(f"iCloud: Apple served the 2FA page with HTTP {status} — parsed it anyway "
             f"(route={boot_context.auth_initial_route!r}, factors={list(boot_context.auth_factors)})")

    auth_options.update(boot_context.as_auth_data())
    api._hsa2_boot_context = boot_context
    api._clear_trusted_device_bridge_state()
    api._set_two_factor_delivery_state("unknown")
    return auth_options


class _PyiCloudLoggingHandler(logging.Handler):
    """Routes pyicloud's stdlib logging into Floxy's logging system.
    pyicloud logs the interesting 2FA details (request URLs, 'Code verification
    failed.', 'Session trust failed.') to its own logger, which otherwise never
    reaches floxy.out."""

    def emit(self, record):
        if record.getMessage().startswith("Saved "):
            return  # "Saved session/cookies data to file" after every request: a save that failed warns on its own
        msg = self.format(record)
        if record.levelno >= logging.ERROR:
            error(f"[pyicloud] {msg}")
        elif record.levelno >= logging.INFO:
            trace(f"[pyicloud] {msg}")
        else:
            fine(f"[pyicloud] {msg}")


def _setup_pyicloud_logging():
    handler = _PyiCloudLoggingHandler()
    handler.setFormatter(logging.Formatter('%(name)s: %(message)s'))
    logger = logging.getLogger('pyicloud')
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


_setup_pyicloud_logging()


class iCloudAPIError(Exception):
    """Base class for iCloud API related errors."""


class iCloudConfigurationError(iCloudAPIError):
    """Raised when required iCloud configuration is missing or invalid."""


class iCloudAuthenticationError(iCloudAPIError):
    """Raised when authentication fails or 2FA is required."""


@dataclass
class iCloudDeviceSpec:
    id: str  # Find My's `id` (what pyicloud keys devices on); older entries hold the baUUID
    display_name: str  # Human-friendly name for voice commands

    def label(self) -> str:
        """Return the best label to use for human facing messages."""
        return self.display_name

    def matches(self, data: Dict[str, Any]) -> bool:
        """Whether a Find My record is this device. The record's `id` is the identity; the
        baUUID is accepted for entries written before 2026-10, when a phone re-enrolled in
        Find My showed the field can go blank while the record itself stays."""
        return self.id in (data.get("id"), data.get("baUUID"))


class ICloudIntegration(Integration):
    """Find My: where the family's devices are, and a ring for a lost phone.

    Locates the family's devices through iCloud Find My and makes a lost phone ring. Signing in
    takes the Apple ID and the 2FA code Apple texts.

    Owns the pyicloud session, including automatic SMS 2FA recovery with strict caps on code
    requests and fresh signins so a broken flow can never spam Apple or the phone."""
    sensitive = True

    SESSION_EXPIRED_2FA_MESSAGE = (
        "iCloud session expired. Please complete 2FA in the TUI console (iCloud tab)."
    )
    NO_DEVICES_MESSAGE = (
        "No iCloud devices are configured yet: icloud_devices() lists the devices the account sees "
        "and the config keys that add one."
    )

    # pyicloud's AppleDevice.location is a cached property — it never asks the
    # device for a fresh fix. Without an active locate we only ever see Apple's
    # last-known position, which can be many minutes stale (isOld). When a fix
    # comes back stale we trigger a locate and poll for up to this long; the
    # device responds asynchronously so it needs a few seconds to report in.
    _LOCATE_TIMEOUT_SECONDS = 10.0
    _LOCATE_POLL_INTERVAL_SECONDS = 2.0

    # Automatic SMS 2FA: cap unattended code requests so a broken flow can't
    # SMS-bomb the phone; give a requested code this long to arrive before
    # considering it lost and allowing a new request.
    AUTO_SMS_MAX_REQUESTS = 4
    AUTO_SMS_WINDOW_SECONDS = 30 * 60
    SMS_CODE_WAIT_SECONDS = 5 * 60
    # After a failed recovery (e.g. Apple throttling SMS with -28248), back off
    # for this long. The limiter alone can't stop the spam: the monitor retries
    # every 15 min, which stays under 4-per-30-min forever — each retry doing a
    # full SRP signin and firing a Telegram alert.
    AUTO_SMS_FAILURE_COOLDOWN_SECONDS = 2 * 60 * 60
    # Hard cap on from-scratch password (SRP) signins per recovery, independent
    # of the SMS limiter: on 2026-08-24 a 409 on every submitted code drove four
    # password logins + four SMS in 63s before the limiter kicked in.
    MAX_FRESH_SIGNINS_PER_RECOVERY = 1

    def __init__(
        self,
        config_manager: Optional[ConfigManager] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self._config_manager = config_manager or ConfigManager()
        self._config = self._load_config(config)
        self._validate_config()
        self._api: Optional[PyiCloudService] = None
        self._auth_error: Optional[str] = None
        self._terms_notified = False
        self._auto_2fa_message: Optional[str] = None
        self._sms_requested_at: Optional[float] = None
        self._auto_sms_limiter = RateLimiter(self.AUTO_SMS_MAX_REQUESTS, self.AUTO_SMS_WINDOW_SECONDS)
        self._auto_sms_last_failure_ts = 0.0
        # Serializes auto SMS requests: the photo downloader's worker threads can
        # all hit an auth error at once, and without the lock each would fire its
        # own SMS request before the first one registers as pending.
        self._auto_sms_lock = threading.Lock()
        self._stale_session_rejection = False
        # Only challenges raised by our own fresh password signin are known to
        # accept code validation — codes requested on token-restored sessions get
        # rejected with 409 'Authentication required for Account' (observed
        # 2026-08-20), so requesting on them wastes an SMS.
        self._challenge_from_fresh_signin = False
        self._fresh_signins_this_recovery = 0
        # True only while Floxy runs a signin it intends to complete with a code
        # (startup auth, fresh recovery signin). pyicloud calls _request_2fa_code
        # by itself after ANY SRP signin that ends in a 2FA challenge — including
        # the find-my-iphone re-auth that _is_session_functional's devices probe
        # forces on every restart of a working-but-untrusted session, which sent
        # one SMS per server restart (Aug 31–Sep 1) that nothing ever submitted.
        self._signin_sms_wanted = False

    @property
    def name(self) -> str:
        return "iCloud"

    @property
    def config_keys(self) -> List[str]:
        return ["icloud"]

    def check_health(self) -> bool:
        self._ensure_authenticated()
        return not self.requires_2fa()

    def needs_2fa(self) -> bool:
        return self.requires_2fa()

    def _load_config(self, override_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if override_config is not None:
            return override_config

        try:
            return self._config_manager.get("icloud")
        except KeyError as exc:
            raise iCloudConfigurationError(
                "Missing 'icloud' section in configuration."
            ) from exc

    def _validate_config(self):
        if not self._config.get("apple_id"):
            raise iCloudConfigurationError(
                "Missing iCloud configuration value: apple_id"
            )
        if not self._config.get("password"):
            raise iCloudConfigurationError(
                "Missing iCloud configuration value: password"
            )

        if not self._config.get("devices"):
            info("iCloud: no devices configured — the session still starts (photos, device listing); "
                 "find_phone / locate_user need icloud.devices (icloud_devices() shows how to fill it)")

    @property
    def _device_specs(self) -> List[iCloudDeviceSpec]:
        """The usable configured devices; empty when none are configured yet. A half-written
        entry (id set, display_name still to come) is skipped, not fatal."""
        entries = self._config.get("devices") or []
        if not entries:
            return []
        specs: List[iCloudDeviceSpec] = []
        for entry in entries:
            if not isinstance(entry, dict):
                error(f"Invalid iCloud device configuration entry: {entry}")
                continue
            if not entry.get("id"):
                error(f"iCloud device missing 'id': {entry}")
                continue
            if not entry.get("display_name"):
                error(f"iCloud device missing 'display_name': {entry}")
                continue
            specs.append(
                iCloudDeviceSpec(
                    id=entry.get("id"),
                    display_name=entry.get("display_name"),
                )
            )
        if not specs:
            error("No valid iCloud device configuration entries were found.")
        return specs

    def _select_device_spec(self, device_name: Optional[str]) -> iCloudDeviceSpec:
        """Find device spec by display_name (used in voice commands)."""
        specs = self._device_specs
        if not specs:
            raise iCloudConfigurationError(self.NO_DEVICES_MESSAGE)
        if device_name:
            target = device_name.strip().lower()
            for spec in specs:
                if target in spec.display_name.lower() or spec.display_name.lower() in target:
                    return spec
            raise iCloudConfigurationError(
                f"No iCloud device matches '{device_name}'. Available: {', '.join(s.display_name for s in specs)}"
            )
        return specs[0]

    @staticmethod
    def _session_dir() -> str:
        """Where pyicloud persists the session token, trust token and cookies.
        pyicloud defaults to the per-user temp dir, which macOS wipes on reboot;
        every reboot then cost a from-scratch password signin plus a 2FA round
        (2026-08-17/20/30). One-time migration: adopt the temp-dir session if the
        persistent dir is still empty, so the switch itself does not force a signin."""
        target = ICLOUD_SESSION_DIR
        target.mkdir(parents=True, exist_ok=True)
        if not any(target.iterdir()):
            legacy = pathlib.Path(resolve_cookie_directory())
            files = [f for f in legacy.iterdir() if f.is_file()] if legacy.is_dir() else []
            for f in files:
                shutil.copy2(f, target / f.name)
            if files:
                info(f"iCloud: migrated {len(files)} session file(s) from {legacy} to {target}")
            else:
                trace(f"iCloud: no session files to migrate from {legacy}")
        return str(target)

    def _signin_2fa_code_hook(self, api: PyiCloudService) -> None:
        """Replacement for pyicloud's automatic post-signin code request: SMS-only
        (pyicloud 2.6.x first GETs /verify/trusteddevice, which pops an approval
        dialog on every trusted Apple device), and only when Floxy is running a
        signin it intends to complete with a code — pyicloud fires this after ANY
        SRP signin ending in a 2FA challenge, service re-auths included."""
        if not self._signin_sms_wanted:
            info("iCloud: suppressed pyicloud's automatic post-signin SMS request — "
                 "no 2FA recovery in progress (likely a service re-auth on a "
                 "working-but-untrusted session)")
            return
        _request_sms_code_only(api)

    def _new_service(self, authenticate: bool = True) -> PyiCloudService:
        api = PyiCloudService(
            self._config["apple_id"], self._config["password"],
            cookie_directory=self._session_dir(), authenticate=False)
        api._request_2fa_code = types.MethodType(self._signin_2fa_code_hook, api)
        api._get_mfa_auth_options = types.MethodType(_get_mfa_auth_options_tolerant, api)
        if authenticate:
            api.authenticate()
        return api

    def _auto_sent_phone(self) -> Optional[Dict[str, Any]]:
        """The trusted phone Apple pushed a code to as part of the current
        password signin, or None when the session was restored from a token
        (no live challenge) or the challenge is not an SMS one."""
        api = self._api
        auth_data = getattr(api, "_auth_data", None) if api else None
        if not isinstance(auth_data, dict):
            return None
        pv = auth_data.get("phoneNumberVerification")
        phone = pv.get("trustedPhoneNumber") if isinstance(pv, dict) else None
        if isinstance(phone, dict) and phone.get("pushMode") == "sms":
            return phone
        return None

    def _arm_auto_sent_code(self, signin_started_at: float) -> bool:
        """After a password signin that ended in a 2FA challenge, treat the
        code Apple delivers on its own (pushMode=sms; pyicloud 2.6.x also
        requests it explicitly right after SRP) as the pending SMS request, so
        the iMessage poller submits it. Until 2026-08-30 the flow PUT a second
        /verify/phone request on top of that, and Apple answered the code
        with 409 every time — plus the extra text. Returns True when armed."""
        api = self._api
        if not api or not api.requires_2fa:
            return False
        phone = self._auto_sent_phone()
        if not phone:
            trace("iCloud: signin left no SMS challenge to wait on")
            return False
        api._auth_data["trustedPhoneNumber"] = phone
        api._two_factor_delivery_method = "sms"
        self._sms_requested_at = signin_started_at
        info(f"iCloud: password signin needs 2FA — waiting for the code Apple sends to "
             f"{phone.get('obfuscatedNumber', '???')} on its own (no extra SMS request)")
        return True

    def _ensure_authenticated(self):
        """Ensure we have an authenticated API connection."""
        if self._api is not None:
            return

        try:
            info("Authenticating to iCloud using user id "+self._config["apple_id"])
            started_at = time.time()
            self._signin_sms_wanted = True
            try:
                self._api = self._new_service()
            finally:
                self._signin_sms_wanted = False
            self._auth_error = None
            self._fresh_signins_this_recovery = 0
            # A token-restored session carries no live challenge; only a password
            # signin (no/expired session file) does — and that one is as good as
            # any fresh signin, its auto-sent code included.
            self._challenge_from_fresh_signin = self._arm_auto_sent_code(started_at)
            info("iCloud authentication successful")
        except PyiCloudFailedLoginException as e:
            self._auth_error = str(e)
            error(f"iCloud: Failed to authenticate: {_brief(e)}")
            self._notify_if_terms_of_service(e)
            raise iCloudAuthenticationError(f"Failed to authenticate: {e}") from e

    def _is_session_functional(self) -> bool:
        """Test if the API session actually works despite requires_2fa being True.
        PyiCloud can report requires_2fa=True when hsaTrustedBrowser is False,
        even when the session cookies are valid and API calls succeed."""
        if not self._api:
            return False
        try:
            _ = list(self._api.devices)
            trace("iCloud session is functional despite requires_2fa=True")
            return True
        except Exception as e:
            # This is the error that decides "needs 2FA" — keep it visible. On
            # 2026-08-24 it was swallowed here and the trigger of a 4x SMS loop
            # was never logged.
            info(f"iCloud session is NOT functional (requires_2fa={self._api.requires_2fa}): "
                 f"{type(e).__name__}: {e}")
            return False

    def requires_2fa(self) -> bool:
        """Check if 2FA verification is required."""
        try:
            self._ensure_authenticated()
            if not self._api:
                return False
            if not self._api.requires_2fa:
                return False
            # requires_2fa can be a false positive - verify by testing the session
            if self._is_session_functional():
                return False
            return True
        except iCloudAuthenticationError:
            return False

    def request_sms_2fa(self) -> str:
        """Request a 2FA code via SMS. Returns the obfuscated phone number.
        Must be called before send_2fa_code() so the SMS validation path is used."""
        if not self._api:
            raise iCloudAuthenticationError("Not authenticated")

        phone_verification = self._api._auth_data.get("phoneNumberVerification", {})
        phone = phone_verification.get("trustedPhoneNumber")
        if not phone:
            trace("iCloud: auth data carries no phone challenge — this happens when the last "
                  "authentication completed via session token instead of a password signin; "
                  f"auth_data keys: {sorted(self._api._auth_data.keys())}")
            raise iCloudAuthenticationError("No trusted phone number available for SMS")

        from pyicloud.const import CONTENT_TYPE_JSON, CONTENT_TYPE_TEXT
        headers = self._api._get_auth_headers(
            {"Accept": f"{CONTENT_TYPE_JSON}, {CONTENT_TYPE_TEXT}"}
        )
        # Capture before the PUT so a code that lands while the request is still
        # in flight isn't rejected as predating the request; assign only on
        # success so a failed PUT doesn't leave a phantom pending request.
        requested_at = time.time()
        resp = self._api.session.put(
            "https://idmsa.apple.com/appleauth/auth/verify/phone",
            json={"phoneNumber": self._phone_payload(phone), "mode": "sms"},
            headers=headers,
        )
        if resp.status_code != 200:
            raise iCloudAuthenticationError(f"SMS request failed (HTTP {resp.status_code})")

        self._api._auth_data["mode"] = "sms"
        self._api._auth_data["trustedPhoneNumber"] = phone
        # pyicloud 2.5.0: set delivery method so validate_2fa_code uses the SMS
        # path instead of the trusted device bridge path
        self._api._two_factor_delivery_method = "sms"

        obfuscated = phone.get("obfuscatedNumber", "???")
        self._sms_requested_at = requested_at
        info(f"iCloud: SMS 2FA code requested, sent to {obfuscated}")
        return obfuscated

    @staticmethod
    def _phone_payload(phone: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"id": phone["id"]}
        if isinstance(phone.get("nonFTEU"), bool):
            payload["nonFTEU"] = phone["nonFTEU"]
        return payload

    def sms_request_pending(self) -> bool:
        """True while a requested SMS code is still expected to arrive.
        The TUI uses this to avoid firing a second request (which would
        invalidate the code already in flight) when the user gets impatient."""
        requested = self._sms_requested_at
        return requested is not None and time.time() - requested < self.SMS_CODE_WAIT_SECONDS

    def on_needs_2fa(self):
        """Called by IntegrationMonitor while the session needs 2FA — drive the
        hands-off flow: request an SMS code, which the iMessage poller then
        auto-submits when it lands."""
        if self.sms_request_pending():
            trace("iCloud: SMS 2FA code already requested, waiting for it to arrive via iMessage")
            return
        if self._sms_requested_at is not None:
            info("iCloud: requested SMS 2FA code never arrived, requesting a new one")
        self._auto_request_sms("session needs 2FA")

    def _fresh_password_signin(self):
        """Sign in from scratch with the account password, discarding the persisted
        session token and cookies first. Re-auth via the saved session token can
        succeed for basic web APIs while carrying no live MFA challenge (auth_data
        comes back empty), and Apple rejects any SMS code submitted against such a
        session with 409 'Authentication required for Account' — without ever
        evaluating the code. Only a password (SRP) signin raises a challenge that
        codes can actually validate against."""
        info("iCloud: fresh password signin — discarding persisted session state")
        self._api = None
        self._sms_requested_at = None
        api = self._new_service(authenticate=False)
        api.session.clear_persistence()
        started_at = time.time()
        self._signin_sms_wanted = True
        try:
            api.authenticate()
        except PyiCloudFailedLoginException as e:
            self._auth_error = str(e)
            error(f"iCloud: fresh password signin failed: {_brief(e)}")
            raise iCloudAuthenticationError(f"Fresh signin failed: {e}") from e
        finally:
            self._signin_sms_wanted = False
        self._api = api
        self._auth_error = None
        self._challenge_from_fresh_signin = True
        info(f"iCloud: fresh password signin complete "
             f"(requires_2fa={api.requires_2fa}, trusted={api.is_trusted_session})")
        self._arm_auto_sent_code(started_at)

    def _recovery_failed(self, why: str):
        """Give up on the current auto-recovery: enter the failure cooldown (without
        it the monitor retries — SRP signin + alert — every 15 min for as long as
        Apple keeps refusing) and tell the user."""
        error(f"iCloud: auto-2FA recovery failed — {why}")
        self._auto_sms_last_failure_ts = time.time()
        # This recovery is over — give the next one (after the cooldown) its own
        # fresh-signin budget. A surviving counter would trip the cap check
        # before any work, re-fail, re-arm the cooldown, and alert every 2h
        # forever without ever attempting another signin.
        self._fresh_signins_this_recovery = 0
        system_note(self.name, f"iCloud auto-2FA could not recover: {why}. Auto-recovery paused for "
                               f"{self.AUTO_SMS_FAILURE_COOLDOWN_SECONDS // 3600}h. Manual attention "
                               "needed in the Keep TUI console (iCloud tab).")

    def _auto_request_sms(self, reason: str, fresh_signin: bool = False):
        """Request an SMS 2FA code without user interaction. Falls back to a fresh
        password signin (dropping the persisted session token) when the current
        session cannot produce a usable challenge — pass fresh_signin=True to go
        straight there, e.g. after Apple rejected a code with 'Authentication
        required for Account', which means codes on this session are doomed."""
        with self._auto_sms_lock:
            if self.sms_request_pending():
                trace("iCloud: SMS 2FA request already pending, not requesting another")
                return
            limit = self._auto_sms_limiter.check()
            if limit:
                info(f"iCloud: not auto-requesting SMS 2FA code ({limit}) — "
                     "request manually in the TUI iCloud tab (Ctrl-E) if needed")
                return
            since_failure = time.time() - self._auto_sms_last_failure_ts
            if since_failure < self.AUTO_SMS_FAILURE_COOLDOWN_SECONDS:
                info(f"iCloud: not auto-requesting SMS 2FA code — last recovery attempt "
                     f"failed {int(since_failure // 60)}m ago, in cooldown for another "
                     f"{int((self.AUTO_SMS_FAILURE_COOLDOWN_SECONDS - since_failure) // 60)}m "
                     "(a manual request in the TUI iCloud tab still works)")
                return
            if not fresh_signin and not self._challenge_from_fresh_signin:
                info("iCloud: current auth challenge did not come from a fresh password "
                     "signin — codes requested on it get rejected, going straight to "
                     "a fresh signin")
                fresh_signin = True
            if not fresh_signin:
                try:
                    self._ensure_authenticated()
                    if not self._api or not self._api.requires_2fa:
                        trace("iCloud: no 2FA challenge active, nothing to auto-request")
                        return
                    obfuscated = self.request_sms_2fa()
                except Exception as e:
                    info(f"iCloud: SMS request on existing session failed ({_brief(e)}), "
                         "falling back to a fresh password signin")
                    fresh_signin = True
            if fresh_signin:
                if self._fresh_signins_this_recovery >= self.MAX_FRESH_SIGNINS_PER_RECOVERY:
                    self._recovery_failed(
                        f"already did {self._fresh_signins_this_recovery} fresh password "
                        "signin(s) this recovery and the session still cannot validate a "
                        "code — not signing in again")
                    return
                try:
                    self._fresh_signins_this_recovery += 1
                    self._fresh_password_signin()
                    if not self._api or not self._api.requires_2fa:
                        info("iCloud: session no longer requires 2FA after fresh signin")
                        return
                    if self.sms_request_pending():
                        obfuscated = (self._auto_sent_phone() or {}).get("obfuscatedNumber", "???")
                        reason += ", using the code sent by the signin itself"
                    else:
                        obfuscated = self.request_sms_2fa()
                except Exception as retry_err:
                    self._recovery_failed(
                        f"SMS request failed even after a fresh password signin ({_brief(retry_err)})")
                    return
            self._auto_sms_limiter.record()
            self._auto_sms_last_failure_ts = 0.0
            info(f"iCloud: auto-requested SMS 2FA code ({reason}), sent to {obfuscated} — "
                 "will auto-submit when it arrives via iMessage")

    def send_2fa_code(self, code: str) -> bool:
        """
        Send 2FA verification code.
        Returns True if verification succeeded.
        """
        api = self._api
        if not api:
            raise iCloudAuthenticationError("Not authenticated")

        self._stale_session_rejection = False
        trace(f"iCloud: validating 2FA code, delivery_method={api.two_factor_delivery_method}")
        if api.two_factor_delivery_method == "sms":
            result = self._validate_sms_code_verbose(api, code)
        else:
            try:
                result = api.validate_2fa_code(code)
            except Exception as e:
                error(f"iCloud: 2FA validation exception: {_brief(e)}")
                return False
            trace(f"iCloud: validate_2fa_code returned {result}")
        if result:
            self.state = IntegrationState.OK
            self._sms_requested_at = None
            self._fresh_signins_this_recovery = 0
            info("iCloud: 2FA verification successful")
            if not api.is_trusted_session:
                info("iCloud: Trusting session...")
                api.trust_session()
        else:
            error("iCloud: 2FA verification failed "
                  f"(requires_2fa={api.requires_2fa}, requires_2sa={api.requires_2sa}, "
                  f"is_trusted_session={api.is_trusted_session})")
        return result

    def _validate_sms_code_verbose(self, api: PyiCloudService, code: str) -> bool:
        """Mirror of pyicloud 2.5.0's validate_2fa_code() SMS branch, with the
        diagnostics the library swallows: validate_2fa_code() catches the HTTP
        error and returns a bare False, which cannot distinguish 'Apple rejected
        the code' from 'code accepted but the trust step failed' — and the
        recovery for those two states is opposite (a rejected code needs a new
        SMS, a failed trust must NOT request one against the consumed challenge)."""
        try:
            api._validate_sms_code(code)
        except PyiCloudAPIResponseException as e:
            body = (e.response.text or "")[:2000] if e.response is not None else ""
            if self._code_accepted_despite_status(e):
                # Apple answers an ACCEPTED code on this account's SMS flow with
                # HTTP 409 (the challenge form echoed back, no service_errors) and
                # hands out the new session token in the response headers. pyicloud
                # reads that as a failure and never trusts the session; the
                # 2026-08-30 probe showed 2sv/trust succeeding right after it.
                info(f"iCloud: Apple returned HTTP {e.code} to the SMS 2FA code but issued a "
                     "new session token — the code was accepted, continuing to trust")
            else:
                # A real rejection carries service_errors (-21669 'Incorrect
                # Verification Code') or comes without a session token: the code
                # was wrong, superseded, or the challenge behind it is gone.
                self._stale_session_rejection = (
                    e.code == 409
                    and "authentication required" in str(e.reason or "").lower())
                error(f"iCloud: Apple rejected the SMS 2FA code — "
                      f"reason={e.reason!r} code={e.code} "
                      f"stale_session={self._stale_session_rejection} body={body}")
                return False
        except Exception as e:
            error(f"iCloud: SMS 2FA code submission failed: {_brief(e)}")
            return False
        finally:
            api._clear_trusted_device_bridge_state()

        info("iCloud: SMS 2FA code accepted by Apple, trusting session...")
        trusted = api.trust_session()
        if not trusted:
            error("iCloud: 2FA code was ACCEPTED but the session trust step failed — "
                  "the challenge is consumed, so requesting another SMS code will not help")
        return not api.requires_2sa

    @staticmethod
    def _code_accepted_despite_status(e: PyiCloudAPIResponseException) -> bool:
        """True when a non-2xx answer to /verify/phone/securitycode is Apple's
        'accepted, carry on' (new X-Apple-Session-Token, no service_errors) rather
        than a rejection."""
        resp = e.response
        token = resp.headers.get("X-Apple-Session-Token") if resp is not None else None
        if not isinstance(token, str) or not token:
            return False
        try:
            data = resp.json()
        except ValueError:
            return True
        return not (isinstance(data, dict) and (data.get("hasError") or data.get("service_errors")))

    def submit_auto_2fa_code(self, code: str, received_at: Optional[float] = None):
        """Auto-submit a 2FA code detected from iMessage. Called from iMessage polling thread.

        Only codes received after the most recent request_sms_2fa() call are accepted,
        and only one code is submitted per request — Apple invalidates outstanding codes
        when a new one is requested, and a failed attempt can sour the whole challenge,
        so guessing with stale codes makes the legit code fail too."""
        if not self._api or not self._api.requires_2fa:
            trace("iCloud: ignoring auto-detected 2FA code — not awaiting 2FA")
            return
        requested_at = self._sms_requested_at
        if requested_at is None:
            info("iCloud: ignoring auto-detected 2FA code — no SMS request pending "
                 "(code was triggered elsewhere, e.g. a signin auto-send or another device)")
            return
        if received_at is not None and received_at < requested_at:
            info(f"iCloud: ignoring auto-detected 2FA code — it arrived {requested_at - received_at:.0f}s "
                 "before the current SMS request, so it belongs to an earlier attempt")
            return
        self._sms_requested_at = None  # single-shot: one submission per SMS request
        info(f"iCloud: auto-detected 2FA code from iMessage, submitting...")
        try:
            result = self.send_2fa_code(code)
            if result:
                self._auto_2fa_message = "2FA code auto-detected from iMessage — verified!"
                system_note(self.name, "iCloud 2FA code auto-detected from iMessage and verified. ✅")
                from assistant.integration.IntegrationMonitor import IntegrationMonitor
                IntegrationMonitor.get_instance().check_now("iCloud")
            else:
                self._auto_2fa_message = "2FA code auto-detected but verification failed"
                if self._stale_session_rejection and self._challenge_from_fresh_signin:
                    self._stale_session_rejection = False
                    # The 409 hit a challenge our own password signin raised seconds
                    # ago, so 'stale session' is the wrong diagnosis and repeating
                    # the recipe cannot help — on 2026-08-24 it looped four times
                    # (4 SRP signins + 4 SMS in 63s) until the SMS limiter tripped.
                    self._recovery_failed(
                        "Apple answered 409 'Authentication required for Account' to a code "
                        "on a challenge from a fresh password signin — retrying would loop")
                elif self._stale_session_rejection:
                    self._stale_session_rejection = False
                    # The session is dead, not the code — another code on this
                    # session is equally doomed, so recover with a from-scratch
                    # password signin and request the next code on that challenge.
                    system_note(self.name, "iCloud 2FA code auto-detected from iMessage but Apple rejected it "
                                           "on a stale session — signing in again and requesting a fresh code.")
                    self._auto_request_sms("code rejected on a stale session",
                                           fresh_signin=True)
                else:
                    # The code was stale (superseded by a newer one) or the challenge is
                    # soured — request a fresh code so the flow recovers on its own.
                    system_note(self.name, "iCloud 2FA code auto-detected from iMessage but Apple rejected it — "
                                           "requesting a fresh code.")
                    self._auto_request_sms("previous code was rejected")
        except Exception as e:
            error(f"iCloud: auto 2FA submission failed: {_brief(e)}")
            self._auto_2fa_message = f"2FA auto-submit error: {e}"

    @property
    def auto_2fa_message(self) -> Optional[str]:
        """Return and clear the one-shot auto-2FA status message for the TUI."""
        msg = self._auto_2fa_message
        self._auto_2fa_message = None
        return msg

    def get_auth_status(self) -> Dict[str, Any]:
        """Get current authentication status for TUI display."""
        if self._auth_error:
            return {
                "status": "error",
                "message": self._auth_error,
            }

        if self._api is None:
            return {
                "status": "not_initialized",
                "message": "Not connected to iCloud",
            }

        if self.requires_2fa():
            return {
                "status": "needs_2fa",
                "message": "2FA verification required",
            }

        # Session works for basic APIs but pyicloud still flags requires_2fa —
        # CloudKit needs full 2FA completion to work.
        if self._api.requires_2fa:
            return {
                "status": "partial_auth",
                "message": "Connected — login not fully verified; auto-2FA will fix via SMS if photos break",
            }

        return {
            "status": "authenticated",
            "message": "Connected to iCloud",
        }

    def get_devices_list(self) -> str:
        """Return formatted list of configured device display names for prompt ("" when none)."""
        specs = self._device_specs
        return ", ".join(spec.label() for spec in specs)

    def configured_device_count(self) -> int:
        """How many icloud.devices entries exist — the index the next one gets."""
        return len(self._device_specs)

    def get_icloud_devices(self) -> List[Dict[str, Any]]:
        """Every device the iCloud account sees (not the config), each with `configured_as`
        (the display name it is configured under, or None) and `slot` (its index in
        icloud.devices, or None)."""
        try:
            self._ensure_authenticated()
            if self._api and not self.requires_2fa():
                specs = self._device_specs
                devices = []
                for device in self._api.devices:
                    data = device.data
                    slot, display_name = next(((i, spec.display_name) for i, spec in enumerate(specs)
                                               if spec.matches(data)), (None, None))
                    devices.append({
                        "id": data.get("id", ""),
                        "name": data.get("name", "Unknown"),
                        "model": data.get("deviceDisplayName", ""),
                        "class": data.get("deviceClass", ""),
                        "configured_as": display_name,
                        "slot": slot,
                    })
                fine(f"iCloud: account sees {len(devices)} device(s), {len(specs)} configured")
                return devices
        except Exception as e:
            error(f"Failed to get iCloud devices: {_brief(e)}")
            self._notify_if_terms_of_service(e)
        return []

    def _notify_if_terms_of_service(self, exc: Exception):
        """Apple blocks the account until the user accepts new terms — nothing the server
        can do, so tell the user once where to go."""
        if "terms of service" not in str(exc).lower():
            return
        if self._terms_notified:
            trace("iCloud: terms-of-service block already reported, not repeating")
            return
        self._terms_notified = True
        info("iCloud: Apple requires accepting updated terms of service — notifying the user")
        system_note(self.name,
            "iCloud is blocked until you accept Apple's updated terms of service. "
            "Sign in at https://icloud.com (or open Settings on an Apple device) and accept them."
        )

    def _notify_2fa_required(self):
        """Send notification when 2FA is required."""
        system_note(self.name, "iCloud session expired. Attempting automatic SMS 2FA — "
                               "manual entry available in Keep TUI console (iCloud tab) if it doesn't recover.")

    @staticmethod
    def _is_reauthentication_error(exc: Exception) -> bool:
        error_str = str(exc).lower()
        return "re-authentication required" in error_str or ("authentication" in error_str and "required" in error_str)

    def _mark_session_needs_2fa(self):
        self.state = IntegrationState.NEEDS_2FA
        self._notify_2fa_required()
        self._api = None
        self._auth_error = "Session expired - 2FA required"

    def _ensure_authenticated_and_ready(self):
        try:
            self._ensure_authenticated()
        except iCloudAuthenticationError:
            self._notify_2fa_required()
            raise

        if self._api and self.requires_2fa():
            self._mark_session_needs_2fa()
            raise iCloudAuthenticationError(self.SESSION_EXPIRED_2FA_MESSAGE)

    def _find_target_device(self, spec: iCloudDeviceSpec):
        target_device = next((d for d in self._api.devices if spec.matches(d.data)), None)
        if not target_device:
            available = [f"{d.data.get('name')} ({d.data.get('deviceDisplayName') or d.data.get('deviceClass') or '?'}, "
                         f"id {(d.data.get('id') or '')[:8]}…)" for d in self._api.devices]
            info(f"iCloud: configured device '{spec.label()}' (id {spec.id[:8]}…) is not among the "
                 f"{len(available)} the account sees — its id may have changed; icloud_devices() shows the current ones")
            raise iCloudAPIError(
                f"Device '{spec.label()}' (id={spec.id}) not found in iCloud. Available: {', '.join(available)}"
            )
        return target_device

    def _run_with_reauthentication(self, operation, failure_context: str):
        try:
            return operation()
        except iCloudAPIError:
            raise
        except Exception as e:
            if not self._is_reauthentication_error(e):
                raise

            info(f"iCloud session expired {failure_context}, attempting re-authentication...")
            self._api = None
            self._ensure_authenticated_and_ready()

            try:
                return operation()
            except iCloudAuthenticationError:
                raise
            except Exception as retry_error:
                error(f"Failed to re-authenticate or retry: {_brief(retry_error)}")
                self._notify_if_terms_of_service(retry_error)
                raise

    def _play_sound_for_spec(self, spec: iCloudDeviceSpec) -> str:
        target_device = self._find_target_device(spec)
        target_device.play_sound()
        return target_device.data.get("name") or spec.label()

    def play_sound(self, device_name: Optional[str]) -> str:
        """Play sound on the specified device (Find My iPhone)."""
        info("Playing sound on iCloud device...")
        self._ensure_authenticated_and_ready()

        spec = self._select_device_spec(device_name)
        played_device_name = self._run_with_reauthentication(
            lambda: self._play_sound_for_spec(spec),
            "during API call"
        )
        note(f"Playing sound on {played_device_name}")

        return f"Playing sound on {spec.label()}"

    def _wait_for_fresh_location(self, spec: iCloudDeviceSpec):
        """Return the device for spec, triggering an active Find My locate and
        polling until it reports a fresh fix (isOld is False) or the timeout
        elapses. pyicloud's AppleDevice.location is a cached property and the
        background monitor thread only pulls last-known positions, so without
        this we'd report stale locations (e.g. "home" long after they left)."""
        deadline = time.time() + self._LOCATE_TIMEOUT_SECONDS
        attempts = 0
        while True:
            target_device = self._find_target_device(spec)
            location = target_device.location
            if not location or not bool(location.get("isOld")):
                if attempts:
                    info(f"iCloud: got a fresh fix for {spec.label()} after {attempts} locate attempt(s)")
                return target_device
            if time.time() >= deadline:
                info(
                    f"iCloud: {spec.label()} location still stale after {attempts} locate attempt(s) "
                    f"({self._LOCATE_TIMEOUT_SECONDS:.0f}s budget) — falling back to last-known fix"
                )
                return target_device
            attempts += 1
            trace(f"iCloud: {spec.label()} fix is stale, requesting active locate (attempt {attempts})")
            try:
                self._api.devices.refresh(locate=True)
            except Exception as e:
                if self._is_reauthentication_error(e):
                    raise
                info(f"iCloud: active locate refresh failed for {spec.label()}: {_brief(e)} — falling back to last-known fix")
                return target_device
            time.sleep(self._LOCATE_POLL_INTERVAL_SECONDS)

    def _location_for_spec(self, spec: iCloudDeviceSpec) -> str:
        """Inner operation: read location for a resolved device spec.
        Wrapped by _run_with_reauthentication so an expired session retries once."""
        target_device = self._wait_for_fresh_location(spec)
        location = target_device.location
        if not location:
            note(f"iCloud: location unavailable for {spec.label()}")
            return f"Location unavailable for {spec.label()} (device offline, location services off, or no recent fix)."

        latitude = location.get("latitude")
        longitude = location.get("longitude")
        accuracy = location.get("horizontalAccuracy")
        timestamp_ms = location.get("timeStamp")
        is_old = bool(location.get("isOld"))
        is_inaccurate = bool(location.get("isInaccurate"))

        if latitude is None or longitude is None:
            note(f"iCloud: location dict for {spec.label()} missing coordinates: {location}")
            return f"Location unavailable for {spec.label()} (no coordinates in response)."

        trace(f"iCloud {spec.label()}: lat={latitude} lon={longitude} accuracy={accuracy}m old={is_old} inaccurate={is_inaccurate}")

        place = reverse_geocode(latitude, longitude)
        if place:
            info(f"Resolved {spec.label()} to {place}")
        else:
            info(f"iCloud: reverse geocode returned no result for {spec.label()} ({latitude}, {longitude})")

        return self._format_location(
            spec.label(),
            place,
            latitude,
            longitude,
            accuracy,
            timestamp_ms,
            is_old,
            is_inaccurate,
        )

    @staticmethod
    def _format_location(
        label: str,
        place: Optional[str],
        latitude: float,
        longitude: float,
        accuracy: Optional[float],
        timestamp_ms: Optional[float],
        is_old: bool,
        is_inaccurate: bool,
    ) -> str:
        headline = f"{label}: {place}" if place else f"{label}: location unavailable as a place name"
        if is_old:
            # Lead with staleness so the assistant doesn't confidently report an
            # out-of-date position as the person's current whereabouts.
            headline += " (STALE — last-known location, the device has not reported a fresh fix)"
        lines = [headline]

        accuracy_str = f"±{int(round(accuracy))}m" if accuracy is not None else "accuracy unknown"
        lines.append(f"GPS: {latitude:.5f}, {longitude:.5f} ({accuracy_str})")

        if timestamp_ms is not None:
            try:
                age_seconds = max(0, time.time() - (float(timestamp_ms) / 1000.0))
                lines.append(f"Updated {ICloudIntegration._humanize_age(age_seconds)} ago")
            except (TypeError, ValueError):
                pass

        if is_inaccurate:
            lines.append("Note: fix is inaccurate.")

        return "\n".join(lines)

    @staticmethod
    def _humanize_age(age_seconds: float) -> str:
        if age_seconds < 60:
            return f"{int(age_seconds)} seconds"
        if age_seconds < 3600:
            return f"{int(age_seconds // 60)} minutes"
        if age_seconds < 86400:
            hours = age_seconds / 3600
            return f"{hours:.1f} hours" if hours < 10 else f"{int(hours)} hours"
        days = age_seconds / 86400
        return f"{days:.1f} days" if days < 10 else f"{int(days)} days"

    def get_location(self, device_name: Optional[str]) -> str:
        """Fetch location for the specified device via Find My iPhone."""
        info(f"Fetching iCloud location for device: {device_name!r}")
        self._ensure_authenticated_and_ready()

        spec = self._select_device_spec(device_name)
        return self._run_with_reauthentication(
            lambda: self._location_for_spec(spec),
            "during location lookup"
        )

    @property
    def api(self) -> Optional[PyiCloudService]:
        """Get the authenticated PyiCloudService instance, or None if unavailable."""
        try:
            self._ensure_authenticated()
            if self._api and not self.requires_2fa():
                return self._api
        except Exception:
            pass
        return None

    def refresh_connection(self):
        """Force a new authentication attempt."""
        self._api = None
        self._auth_error = None
        # New signin = new auth challenge; any SMS code in flight for the old
        # session is dead, so don't let the poller submit it.
        self._sms_requested_at = None
        self._ensure_authenticated()
        if self._api and not self.requires_2fa():
            self.state = IntegrationState.OK
            info("iCloud connection refreshed, session is functional")
        if self._api and self._api.requires_2fa:
            # Session may work for basic APIs, but the browser isn't trusted
            # (CloudKit/photos broken) — kick off the automatic SMS 2FA flow.
            self._auto_request_sms("session untrusted after refresh")


iCloudAPI = ICloudIntegration
