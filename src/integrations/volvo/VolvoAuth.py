"""The Volvo ID sign-in of the household's own Volvo Cars app: the OAuth 2.0 code flow by paste, and the token file it leaves behind.

Volvo issues the app the household publishes on its developer portal a client id and a
client secret, and its sample runs the authorization code flow with PKCE on top of that
secret, so this does both: the authorization URL carries an S256 challenge and the token
endpoint gets the verifier and the client credentials as HTTP Basic auth. The portal's app
only redirects to the URIs registered for it, so the household registers the loopback
`REDIRECT_URI` — a page nothing serves — and the flow is loopback-by-paste like Google's:
`begin()` builds the URL, the owner signs in with it in any browser, lands on the loopback
address that never loads and pastes it back into `finish()`, which checks the state and
exchanges the code (`assistant.integration.OAuthPaste` does the paste handling).

The tokens live in `cache/oauth/volvo.json` (mode 600). Access tokens last about half an
hour, so `token()` refreshes within a minute of the expiry. Volvo rotates refresh tokens:
every refresh answers with a new one and the old one dies, so the newest is written at once
— and kept when an answer carries none, which has happened. A refresh Volvo refuses (the
refresh token lapsed: unused for seven days, or the grant ran out) deletes nothing: the
sign-in is marked as needed again and every call says so until `finish()` succeeds. The
client (`volvo.oauth.client_id` / `client_secret`) is read at call time so a key set through
config_request works without a rebuild.

A flow in progress is module state, not instance state: `restart_integration` rebuilds the
integration — a key set through config_request needs it — and the link the owner was given
a minute earlier must still be the one whose answer is pasted back, so a restart between
`begin()` and `finish()` does not kill the sign-in. The flow remembers the client id the link
was built with: a link made for one client id cannot be finished with another (Volvo ID would
refuse the exchange), and `finish()` says to ask for a new link instead."""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

from assistant.integration.OAuthPaste import SignIn, pasted_code
from assistant.integration.VehicleProvider import VehicleError
from common.logging import error, info, trace
from common.Paths import CACHE_DIR

TOKEN_DIR = CACHE_DIR / "oauth"
TOKEN_FILE = "volvo.json"
AUTHORIZE_URL = "https://volvoid.eu.volvocars.com/as/authorization.oauth2"
TOKEN_URL = "https://volvoid.eu.volvocars.com/as/token.oauth2"
# A loopback address nothing serves — registered as the app's redirect URI on the portal;
# the browser lands on it and the user copies it back. Google's sign-in uses 18722.
REDIRECT_PORT = 18723
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/"
# What the state and the climate commands need: the Connected Vehicle API v2 (the car, its
# engine, fuel, statistics and the commands), the Energy API v2 and the Location API.
SCOPES = [
    "openid",
    "conve:vehicle_relation",
    "conve:engine_status",
    "conve:fuel_status",
    "conve:battery_charge_level",
    "conve:trip_statistics",
    "conve:command_accessibility",
    "conve:commands",
    "conve:climatization_start_stop",
    "energy:capability:read",
    "energy:state:read",
    "location:read",
]
REFRESH_MARGIN = 60
DEFAULT_EXPIRES_IN = 1800
TIMEOUT = 30
LOGIN_COMMAND = "volvo_login()"


class NotSignedIn(VehicleError):
    """No usable token: never signed in, signed out, or the refresh was refused."""


@dataclass(frozen=True)
class PendingSignIn:
    """A sign-in link handed out and not yet answered: what `finish()` needs to accept the answer."""
    state: str
    verifier: str
    client_id: str
    started_at: float


# The flow in progress outlives the VolvoAuth instance (see the module docstring); one Volvo ID.
_pending: Optional[PendingSignIn] = None
_pending_lock = threading.RLock()


def pending_sign_in() -> Optional[PendingSignIn]:
    with _pending_lock:
        return _pending


def _set_pending(flow: Optional[PendingSignIn]) -> None:
    global _pending
    with _pending_lock:
        _pending = flow


def token_path() -> Path:
    return TOKEN_DIR / TOKEN_FILE


def not_signed_in_hint() -> str:
    return f"Volvo is not signed in — {LOGIN_COMMAND}"


def sign_in_again_hint() -> str:
    return f"sign in to Volvo again — {LOGIN_COMMAND}"


def delete_token_file() -> bool:
    """The token file, gone (a sign-out, or the integration lost its config): True when there was one."""
    path = token_path()
    if not path.exists():
        trace("Volvo: no token file to delete")
        return False
    try:
        path.unlink()
        info(f"Volvo: token file {path.name} deleted")
        return True
    except OSError as e:
        error(f"Volvo: could not delete {path}: {e}")
        return False


class VolvoAuth:
    """The one Volvo ID's tokens. `client` returns the current (client_id, client_secret)."""

    def __init__(self, client: Callable[[], tuple[str, str]]):
        self._client = client
        self._tokens: Optional[dict] = None
        self.refresh_error = ""
        self._lock = threading.RLock()
        flow = pending_sign_in()
        if flow is not None:
            info(f"Volvo: a sign-in started {int(time.time() - flow.started_at)} s ago is still in progress — its link stays valid")

    @property
    def path(self) -> Path:
        return token_path()

    # --- the token file ----------------------------------------------------------------------

    def _load(self) -> dict:
        if self._tokens is None:
            self._tokens = {}
            if self.path.exists():
                try:
                    self._tokens = json.loads(self.path.read_text(encoding="utf-8")) or {}
                    trace(f"Volvo: tokens loaded from {self.path.name}")
                except (OSError, ValueError) as e:
                    error(f"Volvo: token file {self.path} unreadable, starting empty: {e}")
            else:
                trace("Volvo: no token file yet")
        return self._tokens

    def _save(self, tokens: dict) -> None:
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tokens, handle)
        os.chmod(self.path, 0o600)
        self._tokens = tokens
        trace(f"Volvo: tokens written to {self.path.name}")

    # --- what the integration reads ----------------------------------------------------------

    def signed_in(self) -> bool:
        """True when a refresh token is on file: a token can be asked for without the user."""
        with self._lock:
            return bool(self._load().get("refresh_token"))

    def needs_sign_in_again(self) -> bool:
        """True when the last refresh was refused: the file is kept, the user must sign in anew."""
        return bool(self.refresh_error)

    def expires_at(self) -> Optional[datetime]:
        """When the access token on file runs out (the next call refreshes it); None without one."""
        expiry = self._load().get("expiry")
        return datetime.fromtimestamp(float(expiry), tz=timezone.utc) if expiry else None

    def token(self) -> str:
        """A current access token, refreshed when within a minute of its expiry; raises NotSignedIn."""
        with self._lock:
            tokens = self._load()
            if not tokens.get("refresh_token"):
                raise NotSignedIn(not_signed_in_hint())
            if self.refresh_error:
                raise NotSignedIn(f"{self.refresh_error}, {sign_in_again_hint()}")
            if tokens.get("access_token") and float(tokens.get("expiry") or 0) - time.time() > REFRESH_MARGIN:
                return tokens["access_token"]
            return self._refresh(tokens)

    def _refresh(self, tokens: dict) -> str:
        trace("Volvo: refreshing the access token")
        answer, status = self._token_request({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        if "access_token" not in answer:
            reason = str(answer.get("error") or "no token")
            description = str(answer.get("error_description") or reason)
            info(f"Volvo: token refresh refused ({status}): {reason} — {description}")
            if status in (400, 401, 403):
                # Volvo answers 400 invalid_grant for a lapsed refresh token (unused for seven days, or
                # the grant ran out) and 401 for a client the portal no longer knows — both need the owner.
                self.refresh_error = f"the Volvo sign-in lapsed ({description})"
            raise NotSignedIn(f"token refresh refused ({description}), {sign_in_again_hint()}")
        updated = {**tokens, "access_token": answer["access_token"],
                   "expiry": time.time() + float(answer.get("expires_in") or DEFAULT_EXPIRES_IN)}
        if answer.get("refresh_token"):
            updated["refresh_token"] = answer["refresh_token"]
            trace("Volvo: the refresh rotated the refresh token, the new one is kept")
        else:
            info("Volvo: the refresh answered without a refresh token — keeping the one on file")
        self._save(updated)
        return updated["access_token"]

    def _token_request(self, data: dict) -> tuple[dict, int]:
        """(the token endpoint's answer, the HTTP status); the client credentials go as Basic auth."""
        client_id, client_secret = self._client()
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        try:
            response = requests.post(TOKEN_URL, data=data, headers={"Authorization": f"Basic {credentials}"}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise NotSignedIn(f"Volvo ID's token endpoint could not be reached: {e}") from e
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 and "error" not in body:
            body = {"error": f"HTTP {response.status_code}", "error_description": (response.text or "")[:200]}
        return (body if isinstance(body, dict) else {}), response.status_code

    # --- the code flow -----------------------------------------------------------------------

    def begin(self) -> SignIn:
        """Starts the flow: the URL for the owner. Raises RuntimeError without a client."""
        client_id, client_secret = self._client()
        if not client_id or not client_secret:
            raise RuntimeError("volvo.oauth.client_id and volvo.oauth.client_secret are not both set")
        with self._lock:
            flow = PendingSignIn(state=secrets.token_urlsafe(16), verifier=secrets.token_urlsafe(64),
                                 client_id=client_id, started_at=time.time())
            challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
            params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
                      "scope": " ".join(SCOPES), "state": flow.state,
                      "code_challenge": challenge, "code_challenge_method": "S256"}
            url = f"{AUTHORIZE_URL}?{urlencode(params)}"
            _set_pending(flow)
        info("Volvo: sign-in started")
        return SignIn(url, flow.state, REDIRECT_URI)

    def pending(self) -> bool:
        """True while a link is out and unanswered — across a rebuild of the integration too."""
        return pending_sign_in() is not None

    def finish(self, pasted: str) -> str:
        """Exchanges the code in the pasted address for the tokens and stores them; "" when
        signed in, else why not."""
        with self._lock:
            flow = pending_sign_in()
            code, problem = pasted_code(pasted, flow.state if flow else None, "Volvo", LOGIN_COMMAND)
            if problem:
                return problem
            if flow.client_id != self._client()[0]:
                info("Volvo: volvo.oauth.client_id changed since the sign-in link was made — the link is dropped")
                _set_pending(None)
                return f"the app's client id changed since the link was made — run {LOGIN_COMMAND} for a new link"
            answer, status = self._token_request({"grant_type": "authorization_code", "code": code,
                                                  "redirect_uri": REDIRECT_URI, "code_verifier": flow.verifier})
            if "access_token" not in answer:
                reason = str(answer.get("error") or "no token")
                description = str(answer.get("error_description") or reason)
                info(f"Volvo: code exchange failed ({status}): {reason} — {description}")
                if reason == "invalid_grant":
                    return f"the code expired or was already used — run {LOGIN_COMMAND} for a new link"
                if status == 401:
                    return f"Volvo ID refused the app's client id and secret ({description}) — check volvo.oauth in config.yaml"
                return f"Volvo ID answered {reason}: {description}"
            if not answer.get("refresh_token"):
                info("Volvo: the code exchange returned no refresh token")
                return f"Volvo ID sent no refresh token — run {LOGIN_COMMAND} again"
            self._save({"refresh_token": answer["refresh_token"], "access_token": answer["access_token"],
                        "expiry": time.time() + float(answer.get("expires_in") or DEFAULT_EXPIRES_IN),
                        "signed_in_at": time.time()})
            _set_pending(None)
            self.refresh_error = ""
        info("Volvo: signed in")
        return ""

    def sign_out(self) -> None:
        """Forgets the sign-in: the token file is deleted."""
        with self._lock:
            self._tokens = None
            _set_pending(None)
            self.refresh_error = ""
            delete_token_file()
