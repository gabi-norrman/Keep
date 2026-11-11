"""Spotify started on a speaker over Spotify Connect: the account's device list, and a play by device id through the Web API.

HEOS cannot start Spotify itself — Spotify is a source of its now-playing (sid 4) but none it can
browse or queue — so the Web API does it on the user token of the shared `SpotifyAccount` (the
playback scopes, Premium). A speaker is reachable only while the account's `/me/player/devices`
lists it: a player another account used last, or one the list forgot, is a 404 even by its exact
id, and is told as such instead of tried. A device matches its player by name (Spotify shows the
name the player has in HEOS, character for character); `heos.spotify_devices` maps a player to
another name where that is not so.

What plays: a playlist or an album as its context, shuffled; an artist as the artist context
(artist radio is not open to new apps); a track inside its album, from that track and in the
album's order (no recommendations for new apps either), so what was asked for is what plays."""
from dataclasses import dataclass
from typing import Optional

from spotipy.exceptions import SpotifyException

from assistant.integration.music.SpotifyAccount import SpotifyAccount
from assistant.integration.music.SpotifyAuth import NotSignedIn, TokenUnavailable
from common.logging import info, trace


@dataclass(frozen=True)
class ConnectDevice:
    """One device of the account's Spotify Connect list."""
    id: str
    name: str
    active: bool = False


class ConnectError(Exception):
    """Spotify could not be started; the message is what the LLM should read."""


class NotListed(ConnectError):
    """The account's device list does not hold the player: another account has it, or the list forgot it."""


class SpotifyConnect:
    """Playback through the Web API on the shared account. `device_names` maps a player name to its Connect name."""

    def __init__(self, account: SpotifyAccount, device_names: Optional[dict] = None):
        self.account = account
        self.device_names = {str(k).lower(): str(v) for k, v in (device_names or {}).items()}

    def devices(self) -> list[ConnectDevice]:
        client = self._user_client()
        try:
            listed = (client.devices() or {}).get("devices") or []
        except SpotifyException as e:
            raise ConnectError(f"Spotify's device list could not be read (HTTP {e.http_status}: {e.msg})") from e
        devices = [ConnectDevice(d["id"], d.get("name") or "", bool(d.get("is_active"))) for d in listed if d.get("id")]
        trace(f"Spotify Connect: {len(devices)} device(s) listed ({', '.join(d.name for d in devices)})")
        return devices

    def connect_name(self, player: str) -> str:
        return self.device_names.get(player.lower(), player)

    def device_for(self, player: str) -> Optional[ConnectDevice]:
        """The listed device of the player, None when the account does not list it."""
        name = self.connect_name(player).lower()
        return next((d for d in self.devices() if d.name.lower() == name), None)

    def play(self, player: str, uri: str) -> str:
        """Starts the URI on the player's device; returns what plays for the log. Raises NotListed
        when the account does not list the player, ConnectError for every other failure."""
        device = self.device_for(player)
        if device is None:
            raise NotListed(f"{player} is not in the Spotify account's device list")
        body = self._body(uri)
        client = self._user_client()
        try:
            client.start_playback(device_id=device.id, **body)
            info(f"Spotify Connect: {uri} started on {player} ({device.id[:8]}…) as {sorted(body)}")
            if ":playlist:" in uri or ":album:" in uri:
                client.shuffle(True, device_id=device.id)
                trace(f"Spotify Connect: shuffle on for {player}")
        except SpotifyException as e:
            raise ConnectError(self._refusal(player, e)) from e
        return uri

    def _body(self, uri: str) -> dict:
        """What `start_playback` gets for the URI: a context, or a track inside its album."""
        if ":track:" not in uri:
            return {"context_uri": uri}
        try:
            album = (self.account.app_client().track(uri.split(":")[-1]).get("album") or {}).get("uri")
        except Exception as e:
            info(f"Spotify Connect: the album of {uri} could not be read ({e}), the track plays alone")
            album = None
        if not album:
            return {"uris": [uri]}
        return {"context_uri": album, "offset": {"uri": uri}}

    def _user_client(self):
        try:
            return self.account.user_client()
        except NotSignedIn as e:
            raise ConnectError(f"Spotify is not signed in for playback ({e})") from e
        except TokenUnavailable as e:
            raise ConnectError(f"Spotify could not be reached for now ({e})") from e

    @staticmethod
    def _refusal(player: str, e: SpotifyException) -> str:
        reason = str(getattr(e, "reason", "") or "")
        if e.http_status == 403 and "PREMIUM" in reason.upper():
            return "Spotify refused: starting music on a speaker needs Spotify Premium"
        if e.http_status == 404:
            return f"{player} is not in the Spotify account's device list"
        return f"Spotify refused to start it on {player} (HTTP {e.http_status}: {e.msg})"
