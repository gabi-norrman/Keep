import threading
import time
from typing import Dict, List, Optional

from assistant.integration.Integration import Integration
from assistant.integration.music.MusicSystem import LIBRARY_REFRESH_SECONDS, MusicSystem, zones_changed
from assistant.integration.music.SpotifyAccount import SpotifyAccount
from common.ConfigManager import ConfigManager
from common.logging import error, info, trace
from integrations.heos.HeosClient import HeosClient, HeosError, message_fields
from integrations.heos.HeosEvents import HeosEvents
from integrations.heos.SpotifyConnect import ConnectError, NotListed, SpotifyConnect


class HeosIntegration(Integration, MusicSystem):
    """Denon and Marantz HEOS speakers: playback, volume, Spotify over Connect.

    Plays Spotify — search hits, the user's own playlists — and the HEOS favorites in any HEOS
    room, with pause, skip and volume, and tells what plays where. A group plays as one: asking
    for any of its rooms acts on the whole group.

    Transport, volume, now-playing, the account's favorites (`H-n`) and each player's own Quick Select
    buttons (`Q-n`, the speaker's slots, played on that speaker even when it is grouped) go over the HEOS CLI (`HeosClient`,
    TCP 1255 of any one device). Spotify goes through the Web API on the shared `SpotifyAccount`
    (`SpotifyConnect`), since HEOS has no Spotify source of its own to start: the account must be
    Premium, signed in with the playback scopes (`want_playback()`, asked for at construction), and
    must list the player among its Connect devices — a player another account used last is told
    as such. A zone is a player by its HEOS name through `heos.room_mapping`; a grouped player acts
    on its group's leader (whose `pid` is the group's `gid`), the device Spotify plays a group on.
    `HeosEvents` follows the change events on a connection of its own, started with the first
    refresh: while it listens, each leader's status is cached and refetched when an event says it
    changed (`pushes_changes()`, so the zones section stops polling), and a change of the players or
    groups drops the cached lists; while it does not, the status is asked for live as before."""

    ID_PREFIXES = ("H-", "Q-")
    FAVORITES_IN_PROMPT = 10
    PLAYERS_MAX_AGE = 60
    FAVORITES_SID = 1028
    REFRESH_INTERVAL_SECONDS = LIBRARY_REFRESH_SECONDS
    VOLUME_STEP_MAX = 10   # the CLI's own limit for volume_up / volume_down

    def __init__(self):
        Integration.__init__(self)
        self.config = ConfigManager()
        self._client: Optional[HeosClient] = None
        self._players: List[dict] = []
        self._players_at = 0.0
        self._favorites: List[str] = []
        self._quickselects: Dict[str, List[tuple[int, str]]] = {}   # player name -> [(slot id, name)]
        self._lock = threading.Lock()
        self._refresh_timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()
        self._events: Optional[HeosEvents] = None
        self._status: Dict[object, str] = {}   # leader pid -> its status line, kept while the events flow
        SpotifyAccount.want_playback()
        self._schedule_refresh(0.1)

    # --- Integration ----------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "HEOS"

    @property
    def config_keys(self) -> List[str]:
        return []

    @property
    def config_section(self) -> str:
        return "heos"

    def check_health(self) -> bool:
        players = self._player_list(fresh=True)
        trace(f"HEOS: {len(players)} player(s) answer")
        return bool(players)

    def _on_reset(self):
        self._stopped.set()
        if self._refresh_timer is not None:
            self._refresh_timer.cancel()
        if self._events is not None:
            self._events.stop()
        if self._client is not None:
            self._client.close()
        info("HEOS dropped: favorites refresh and change events stopped, connection closed")

    # --- the pieces -----------------------------------------------------------------------------

    @property
    def room_mapping(self) -> Dict[str, str]:
        """`heos.room_mapping`: HEOS player name -> the room name the LLM sees; blank hides the player."""
        return self.config.get("heos.room_mapping", {}) or {}

    def client(self) -> HeosClient:
        if self._client is None:
            self._client = HeosClient(str(self.config.get("heos.host", "") or ""))
        return self._client

    @property
    def spotify(self) -> SpotifyAccount:
        return SpotifyAccount.get_instance()

    def connect(self) -> SpotifyConnect:
        return SpotifyConnect(self.spotify, self.config.get("heos.spotify_devices", {}) or {})

    def _player_list(self, fresh: bool = False) -> List[dict]:
        """The players HEOS reports, cached PLAYERS_MAX_AGE seconds (groups change rarely)."""
        with self._lock:
            if not fresh and self._players and time.time() - self._players_at < self.PLAYERS_MAX_AGE:
                return list(self._players)
        players = self.client().command("player/get_players").get("payload") or []
        with self._lock:
            self._players, self._players_at = list(players), time.time()
        return list(players)

    def _room(self, player: dict) -> str:
        name = player.get("name") or ""
        return self.room_mapping.get(name, name)

    def _player(self, zone: str) -> Optional[dict]:
        """The player behind the room name the LLM uses, case aside."""
        wanted = zone.strip().lower()
        return next((p for p in self._player_list() if self._room(p).lower() == wanted), None)

    def _leader(self, player: dict) -> dict:
        """The player a command for this one acts on: the group's leader when it is grouped."""
        gid = player.get("gid")
        if gid is None or gid == player.get("pid"):
            return player
        return next((p for p in self._player_list() if p.get("pid") == gid), player)

    # --- MusicSystem: what is there -------------------------------------------------------------

    def zones(self) -> List[str]:
        return [room for room in (self._room(p) for p in self._player_list()) if room]

    def zone_status(self) -> List[str]:
        """One line per room, in the Sonos format: what its group's leader plays, and whether it plays.
        A leader is asked once however many rooms its group has."""
        lines, asked = [], {}
        for player in self._player_list():
            room = self._room(player)
            if not room:
                continue
            leader = self._leader(player)
            pid = leader.get("pid")
            if pid not in asked:
                asked[pid] = self._status_of(pid)
            lines.append(f"{room} ({asked[pid]})")
        return lines

    def _status_of(self, pid) -> str:
        """The leader's status: the cached line while the events keep it current, else asked for now."""
        if self.pushes_changes():
            with self._lock:
                cached = self._status.get(pid)
            if cached is not None:
                return cached
        status = self._now_playing(pid)
        if self.pushes_changes():
            with self._lock:
                self._status[pid] = status
        return status

    def pushes_changes(self) -> bool:
        return self._events is not None and self._events.connected

    # --- the change events ----------------------------------------------------------------------

    def _start_events(self):
        if self._events is not None or self._stopped.is_set():
            return
        self._events = HeosEvents(lambda: self._client.host if self._client is not None else "",
                                  self._on_event, self._on_events_connected)
        self._events.start()

    def _on_events_connected(self, up: bool):
        """A stream that comes or goes leaves no cached status behind: its lines are only as good as the events."""
        with self._lock:
            self._status.clear()
        zones_changed(self.name)

    def _on_event(self, name: str, fields: dict):
        if name in ("player_state_changed", "player_now_playing_changed"):
            try:
                pid = int(fields.get("pid", ""))
            except ValueError:
                trace(f"HEOS: {name} without a usable pid ({fields}), ignored")
                return
            status = self._now_playing(pid)
            with self._lock:
                before, self._status[pid] = self._status.get(pid), status
            if status == before:
                # HEOS sends most changes twice, and a pause a now-playing event too: nothing new to show.
                trace(f"HEOS: {name} for player {pid}, status unchanged ({status})")
                return
            trace(f"HEOS: {name} for player {pid} — now {status}")
            zones_changed(self.name)
        elif name in ("players_changed", "groups_changed"):
            with self._lock:
                self._players_at = 0.0
                self._status.clear()
            info(f"HEOS: {name.replace('_', ' ')} — the rooms and groups are read again")
            zones_changed(self.name)
        else:
            trace(f"HEOS: event {name} ({fields}) not shown in the prompt, ignored")

    def _now_playing(self, pid) -> str:
        try:
            state = message_fields(self.client().command("player/get_play_state", pid=pid)).get("state")
            media = self.client().command("player/get_now_playing_media", pid=pid).get("payload") or {}
        except HeosError as e:
            error(f"HEOS: player {pid} unreachable ({e})")
            return "unreachable"
        playing = "Playing" if state == "play" else "Paused"
        title, artist = (media.get("song") or "").strip(), (media.get("artist") or "").strip()
        if title and artist:
            return f"{title} by {artist} - {playing}"
        if title or artist:
            return f"{title or artist} - {playing}"
        return playing

    def library_text(self) -> str:
        with self._lock:
            favorites = list(self._favorites)
            quickselects = dict(self._quickselects)
        lines = [f"{name} (ID: H-{index})" for index, name in enumerate(favorites)]
        for player in self._player_list():
            room, slots = self._room(player), quickselects.get(player.get("name") or "")
            if room and slots:
                buttons = ", ".join(f"{name} (ID: Q-{slot})" for slot, name in slots)
                lines.append(f"{room} quick selects: {buttons}")
        return "\n".join(lines)

    def refresh_library(self) -> None:
        """The HEOS favorites, then the shared Spotify library; each guarded on its own."""
        try:
            items = self.client().command("browse/browse", sid=self.FAVORITES_SID).get("payload") or []
            names = [item.get("name") for item in items if item.get("name")]
            if len(names) > self.FAVORITES_IN_PROMPT:
                info(f"HEOS: {len(names)} favorites, the first {self.FAVORITES_IN_PROMPT} get prompt ids")
            with self._lock:
                self._favorites = names[:self.FAVORITES_IN_PROMPT]
            info(f"HEOS: {min(len(names), self.FAVORITES_IN_PROMPT)} favorite(s) listed")
        except HeosError as e:
            info(f"HEOS: favorites not reloaded, the cached list stands — {e}")
        self._refresh_quickselects()
        try:
            self.spotify.refresh_library()
        except Exception as e:
            error(f"HEOS: the Spotify library refresh failed: {e}")

    def _refresh_quickselects(self):
        """Each player's Quick Select slots; a player whose firmware has none (or refuses) has none."""
        try:
            players = self._player_list()
        except HeosError as e:
            info(f"HEOS: quick selects not reloaded, the cached ones stand — {e}")
            return
        found: Dict[str, List[tuple[int, str]]] = {}
        for player in players:
            try:
                slots = self.client().command("player/get_quickselects", pid=player["pid"]).get("payload") or []
            except HeosError as e:
                trace(f"HEOS: {player.get('name')} has no quick selects to list ({e})")
                continue
            listed = [(int(slot["id"]), str(slot.get("name") or f"Quick select {slot['id']}"))
                      for slot in slots if slot.get("id") is not None]
            if listed:
                found[player.get("name") or ""] = listed
        with self._lock:
            self._quickselects = found
        info(f"HEOS: quick selects on {len(found)} player(s)")

    def _schedule_refresh(self, delay: float):
        if self._stopped.is_set():
            return
        self._refresh_timer = threading.Timer(delay, self._refresh_tick)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _refresh_tick(self):
        if self._stopped.is_set():
            trace("HEOS: refresh tick after the integration was dropped, skipped")
            return
        try:
            self.refresh_library()
            self._start_events()
        finally:
            self._schedule_refresh(self.REFRESH_INTERVAL_SECONDS)

    # --- MusicSystem: what it does --------------------------------------------------------------

    def _act(self, zone: str, what: str, command: str, **params) -> str:
        """One CLI command on the zone's leader: "" when done, else the line for the LLM."""
        player = self._player(zone)
        if player is None:
            return f"no HEOS room '{zone}'"
        leader = self._leader(player)
        try:
            self.client().command(command, pid=leader["pid"], **params)
        except HeosError as e:
            info(f"HEOS: could not {what} in {zone}: {e}")
            return f"could not {what} in {zone}: {e}"
        info(f"HEOS: {what} in {zone}" + (f" (the group of {leader.get('name')})" if leader is not player else ""))
        return ""

    def play(self, zone: str) -> str:
        return self._act(zone, "play", "player/set_play_state", state="play")

    def pause(self, zone: str) -> str:
        return self._act(zone, "pause", "player/set_play_state", state="pause")

    def next_track(self, zone: str) -> str:
        return self._act(zone, "skip the track", "player/play_next")

    def adjust_volume(self, zone: str, step: int) -> str:
        """The group's volume for a grouped room, the player's own otherwise."""
        player = self._player(zone)
        if player is None:
            return f"no HEOS room '{zone}'"
        direction = "up" if step > 0 else "down"
        amount = max(1, min(self.VOLUME_STEP_MAX, abs(step)))
        gid = player.get("gid")
        try:
            if gid is not None:
                self.client().command(f"group/volume_{direction}", gid=gid, step=amount)
            else:
                self.client().command(f"player/volume_{direction}", pid=player["pid"], step=amount)
        except HeosError as e:
            info(f"HEOS: could not change the volume in {zone}: {e}")
            return f"could not change the volume in {zone}: {e}"
        info(f"HEOS: volume {direction} {amount} in {zone}" + (" (group)" if gid is not None else ""))
        return ""

    def play_id(self, zone: str, music_id: str) -> str:
        player = self._player(zone)
        if player is None:
            return f"no HEOS room '{zone}'"
        leader = self._leader(player)
        if music_id.startswith("H-"):
            return self._play_favorite(zone, leader, music_id)
        if music_id.startswith("Q-"):
            return self._play_quickselect(zone, player, music_id)
        uri = self._spotify_uri(music_id)
        if uri is None:
            return f"{music_id} is no id of a search hit, a playlist or a HEOS favorite"
        try:
            self.connect().play(leader.get("name") or zone, uri)
        except NotListed:
            info(f"HEOS: {leader.get('name')} is not in the Spotify account's device list, nothing started")
            return (f"{zone} is not reachable for this Spotify account: another account used it last, or "
                    f"Spotify has forgotten it — pick it once in the Spotify app, then ask again")
        except ConnectError as e:
            info(f"HEOS: Spotify could not start {uri} in {zone}: {e}")
            return str(e)
        return ""

    def _spotify_uri(self, music_id: str) -> Optional[str]:
        if SpotifyAccount.is_search_id(music_id):
            return self.spotify.resolve(music_id)
        if music_id.startswith("S-"):
            playlist = self.spotify.playlist(music_id)
            return playlist.uri if playlist else None
        if music_id.startswith("spotify:"):
            return music_id
        return None

    def _play_quickselect(self, zone: str, player: dict, music_id: str) -> str:
        """Presses one of the player's own Quick Select buttons, on that player."""
        try:
            slot = int(music_id[2:])
        except ValueError:
            return f"{music_id} is no quick select id"
        with self._lock:
            slots = [s for s, _ in self._quickselects.get(player.get("name") or "", [])]
        if slot not in slots:
            known = ", ".join(f"Q-{s}" for s in slots) or "none"
            return f"{zone} has no quick select {music_id} (it has: {known})"
        try:
            self.client().command("player/play_quickselect", pid=player["pid"], id=slot)
        except HeosError as e:
            return f"could not play the quick select in {zone}: {e}"
        info(f"HEOS: quick select {music_id} started in {zone}")
        return ""

    def _play_favorite(self, zone: str, leader: dict, music_id: str) -> str:
        try:
            index = int(music_id[2:])
        except ValueError:
            return f"{music_id} is no HEOS favorite id"
        with self._lock:
            count = len(self._favorites)
        if not 0 <= index < count:
            return f"{music_id} is no HEOS favorite (there are {count})"
        try:
            # The CLI numbers the favorites ("presets") from 1, in the HEOS app's order.
            self.client().command("browse/play_preset", pid=leader["pid"], preset=index + 1)
        except HeosError as e:
            return f"could not start the favorite in {zone}: {e}"
        info(f"HEOS: favorite {music_id} started in {zone}")
        return ""