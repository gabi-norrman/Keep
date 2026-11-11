# HEOS & Spotify Connect

Music on Denon/Marantz HEOS speakers, with Spotify played over Spotify Connect, next to the Sonos
backend the `music` integration has today.

## Where it starts

`src/integrations/music/` ("Sonos & Spotify") is one folder over `soco` and `spotipy`:

- `MusicIntegration.py` discovers the Sonos zones (cached 30 min), acts on a group's coordinator,
  maps zone names through `sonos.room_mapping`, and caches three lists every 30 min — Sonos favorites
  (`F-n`), Sonos playlists (`P-n`), the signed-in user's Spotify playlists (`S-n`), 10 each — plus
  short ids (`AA`…) for Spotify search hits.
- Spotify is only the catalog: search runs on the app's client credentials, the library on the user
  token `SpotifyAuth` keeps (`playlist-read-*` scopes, `cache/oauth/spotify.json`). **The speaker plays**:
  a playlist or album is queued through soco's `ShareLinkPlugin`, an artist as an
  `x-sonosapi-radio:` URI, a track as the seed plus the artist's top tracks (gone for development-mode
  apps since February 2026, so mostly the track alone) — Sonos resolves the tracks with the household's
  account linked in the Sonos app.
- `MusicCommands.py`: `play`, `pause`, `next`, `volume_up`/`volume_down` (±4 per group member),
  `search_music`, `play_music(zone, id)`, `spotify_login` / `spotify_login_code`.
- `SonosFavoritesSectionGenerator` (MUSIC) and `MusicZonesSectionGenerator` (SONOS ZONES, polled every
  3 s) are carried by `toolkit.md`.

## Why HEOS does not slot in

1. **HEOS cannot start Spotify itself.** Spotify on HEOS is Spotify Connect only; the HEOS CLI
   (JSON over TCP 1255) has no Spotify source to browse or queue a URI from. Starting music goes
   through the Spotify Web API — `PUT /me/player/play?device_id=…` — which needs Premium, the scopes
   `user-read-playback-state` and `user-modify-playback-state`, and the speaker present in
   `/me/player/devices`. Idle Connect devices are known to drop out of that list; that is the main risk.
2. **Command names have one owner.** `Commands` hosts every discovered `CommandSet`, enabled or not, and
   refuses a name with two owners, so a `heos/` folder (in the tree or in `extra_integration_dirs`)
   cannot also bring `play`, `pause`, …

## Phases

### Phase 0 — spike on real speakers

A throwaway script in `tools/heos_spotify_spike/`, its own token file, nothing of Keep's touched:

- HEOS: discover the players, list players and groups, read now-playing and volume.
- Spotify: list `/me/player/devices` while a speaker plays and after it has been idle a while (an hour,
  a night); start a playlist by `device_id` on a single player and on a group leader; check shuffle.
- Name match: does a HEOS player's name equal its Spotify Connect device name?

The outcome decides the rest: if idle speakers vanish from the device list, HEOS favorites played
through HEOS become the fallback, or a "wake" step is needed.

Results (a household of seven players — Denon Home 150, HEOS 1, HEOS HomeCinema — all on Wi-Fi, one
group of two):

- **Discovery**: SSDP finds every player, but only from a process macOS lets multicast; one started
  from Claude Code gets `EHOSTUNREACH` on the send (the script falls back to a unicast scan of the /24,
  which the sandbox blocks as well). Keep's server starts from Terminal, as `soco.discover()` already
  needs. Any one player's CLI reports every player and group.
- **Idle players answer**: stopped players report state, volume and the last media at once.
- **Spotify is source `sid=4`** in `get_now_playing_media` (the last track of a Connect session stays
  there after stopping), and it is **not among `browse/get_music_sources`** — confirmed: HEOS cannot
  start Spotify content; the Web API must.
- **Groups**: a group's `gid` is its leader's `pid`; the leader is the target for Connect playback.
- Other sources: Favorites (1028), Playlists (1025), History (1026), AUX/HDMI (1027 — a soundbar on
  HDMI ARC plays this; starting music there takes the TV's sound away, so such a player needs care).
- **Connect device list: the risk is real.** With every player idle, `/me/player/devices` listed 2 of the
  7 (presumably the ones used over Connect most recently); the other five cannot be played to by
  `device_id` until something brings them back. Starting music on an idle player therefore needs a wake
  path, or a fallback through HEOS.
- **Names match**: each listed device's name is its HEOS player's name, character for character.
- Spotify's `volume_percent` is 0 for both while HEOS says 12 and 27 — volume goes through HEOS.
- `/me` carries no `product` for a development-mode app, so Premium shows only as the play call's
  answer (403 `PREMIUM_REQUIRED` without it).
- **Playback by `device_id` works** on a listed player: `PUT /me/player/play` with a playlist's
  `context_uri` answered 204, `PUT /me/player/shuffle` 200, and `/me/player` then reported the player
  playing, shuffled — the account is Premium and a development-mode app may drive playback.
- **A player picked in the Spotify app comes back**: after the group leader was played to from a phone
  it was listed (active), and a player used hours earlier was still there — a used player stays
  listed for a while, how long is the overnight `watch`'s answer.
- **A HEOS group is one Connect device under its leader's own name** (not the group's name); the member
  is not listed. Connect playback for a group goes to the leader's device.
- The list holds non-HEOS devices too (the phone): only a device matching a HEOS player is a target.
- **Every player advertises Spotify Connect Zeroconf** (`_spotify-connect._tcp`, instance name = the
  player's MAC + its HEOS name), the four the cloud list leaves out included: they are reachable on the
  LAN while Spotify's list forgets them.
- **A HEOS `play` on a stopped, unlisted player resumes Spotify** (sid 4) — not its own last track but
  the account's current item (an episode last played elsewhere): the player still has the account
  linked. It was **not** back in `/me/player/devices` 3, 7 or 15 s later.
- **The resumed player is invisible to the signed-in account**: while it played, `/me/player` named the
  account's last device (another player, paused, on the account's own last track) and the list still
  left it out. A HEOS resume does not make a player addressable. Since it resumed an item that is not
  the signed-in account's, the likely reason is that **the player is linked to another Spotify account**
  of the household — the cloud list is per account, so "unlisted" may mean "someone else's".
- **Confirmed**: that player had last been used from another household account (a Spotify Jam). A
  Connect player belongs to the account that used it last, and only that account's Web API sees it.
- **The local Spotify Connect info** of a player (`getInfo`) does not name the linked account, but its
  `deviceID` is the player's Web API device id, so the ids of unlisted players are known locally.
- **An unlisted id is refused**: `PUT /me/player/play?device_id=<getInfo deviceID>` of the player
  another account holds answered 404 `Device not found`. The public Web API cannot take a player over.
- **So is every unlisted id**: an unlisted player no other account was known to hold answered the same
  404. The Web API reaches exactly the players the account's list holds.
- _Open_ (does not block Phase 1): how long a player stays listed when nobody else takes it (`watch`).

**Taking a player over** from another account was looked into and is **not built**: the only
working route found relies on Spotify-internal sign-in credentials rather than a public API, which
is not something this integration does.

**Verdict.** Spotify Connect over the Web API is the way to start Spotify on HEOS, for every player the
signed-in account holds, group leaders included. HEOS does transport, volume and now-playing. A player
another account holds cannot be taken over through any public API. So:

- **One account** (the owner's decision): no sign-ins per household member.
- **Taking a player over** from another account, or waking one the list forgot, has no public route
  (an unlisted `device_id` is a 404), so it is not done: such a player is answered with "pick it once
  in the Spotify app".
- **A player no signed-in account holds**: the command says so — pick it once in the Spotify app — or
  plays a HEOS favorite there instead; never a silent failure.
- Never a non-HEOS device from the list (phones, computers) as a target.

### Phase 1 — Spotify apart from Sonos

Search, the library, the short ids and the sign-in become a Spotify piece both backends use. The
playback scopes are asked for only while a Connect backend is enabled — a token lacking scopes is
"sign in again" today, and Sonos-only installs must not be pushed into that.

Done as: core `assistant/integration/music/` (core, since core never imports a folder by name and
both folders import it) — `SpotifyAuth` moved there with its scopes a callable;
`SpotifyAccount` (a `SingletonMixin`) holds the app and user clients, the library cache, its
listeners, the 429 hold-back, the sign-in-again note, search with the short ids, the artist top
tracks. `want_playback()` adds `user-read-playback-state` / `user-modify-playback-state` for the
rest of the process — the HEOS integration calls it. The speaker systems drive
`refresh_library()`; one within `MIN_REFRESH_GAP` (5 min) of the last is skipped. `MusicIntegration`
keeps Sonos and thin delegates for its commands and sections (Phase 2 moves those). The spotipy
pin moved to the root requirements. Nothing the LLM or the user sees changed.

### Phase 2 — a music contract

A `MusicSystem` contract in `assistant/integration/` with a composite (the `CompositeHome` / `Fleet`
pattern): zones, play/pause/next/volume, now-playing, play a Spotify URI, favorites. The playback
commands move to core `Commands.py` and a core `res/toolkits/music.md` with `needs=`. Sonos becomes one
backend and keeps the integration name "Sonos & Spotify" (an `enabled_integrations` contract).

Done as: `assistant/integration/music/MusicSystem.py` — the ABC (zones by the LLM's names, one status
line per zone, the system's own lists as `library_text`, play/pause/next_track/adjust_volume/play_id
returning "" or the failure line for the LLM) and `Speakers` (every ready system's zones as one house,
a room resolved case aside, the same zone name in two systems refused). **A system's own list ids carry
prefixes it declares** (`ID_PREFIXES`: Sonos `F-`, `P-`), Spotify's (`S-n`, the search ids) are any
system's, so two systems' lists never answer to one id and an id of the wrong system is refused by
name. The commands (play, pause, next, volume_up/down, play_music, search_music, spotify_login/_code)
are core's; `sonos_enqueue_and_play` (undocumented) stays in the folder. The toolkit is
`res/toolkits/music.md`, `needs=MusicSystem`; the sections are core's (`MusicLibrarySectionGenerator`,
`MusicZonesSectionGenerator`), gated the same way. A transport failure now reaches the LLM (before, the
command fell silent). The section key `sonos`, the SONOS ZONES heading and the toolkit's Sonos wording
stay until Phase 4 — the prompt is byte-identical for a Sonos house.

### Phase 3 — the `heos/` folder

- Zones are HEOS players and groups, acting on the group leader; `heos.room_mapping`.
- Transport, volume (group volume) and now-playing over the HEOS CLI, which also drives a Connect stream.
- Spotify content through the Web API on the device matched by name (`heos.spotify_devices` overrides).
- No recommendations or top tracks for new apps: a track plays in its album's context, an artist as its
  artist context.
- HEOS favorites (presets: radio …) under a prefix of HEOS's own (`H-n`, its `ID_PREFIXES`), played
  through HEOS.
- HEOS change events (`system/register_for_change_events`) instead of polling for the zones section.
- `pyheos` is async: a loop thread of its own, or a small synchronous client of the line protocol.

Done as `src/integrations/heos/`: `HeosClient` (the line protocol
over one shared connection, one reconnect, SSDP when `heos.host` is blank — no `pyheos`, no new
requirement), `SpotifyConnect` (device list, name match with `heos.spotify_devices`, play by device id:
a context shuffled, an artist unshuffled, a track in its album), `HeosIntegration` ("HEOS", no required
keys, `H-n` favorites, a grouped room acting on its leader, group volume for a group). Not done yet:
special care for a player on an HDMI input. `tools/heos_live_check/` runs the server's code against the
real speakers.

Live (the household's seven players): rooms, what plays and the Spotify Connect list read as expected;
the HEOS account's favorites (`sid=1028`) were empty although the CLI connection is signed in — the
"favorite" the owner meant was a **Quick Select** button of a Denon Home 150, which the CLI does expose
(`player/get_quickselects` → slots 1–3 with their names; `player/play_quickselect`). Those are now
`Q-n` per room (on the player itself, grouped or not) and played on the real speaker. Spotify started on a
listed player by device id (a track inside its album); **pause, resume and skip through the HEOS CLI
act on a Spotify Connect stream** — no Web API transport needed; an unlisted player answered with the
"pick it once in the Spotify app" fact. The HEOS 1 / HomeCinema firmware refuses `get_quickselects`
("Option not supported"); the Denon Home 150s have them.

### Phase 4 — neutral texts and tests

SONOS ZONES → MUSIC ZONES, the toolkit and `res/toolkits/setup.md` texts without Sonos, then
`Toolkits.INDEX_ORDER`, `PromptManager.SECTION_ORDER`, the atlas `GROUPS`, the config fragments, the
tests under `tests/unit/integrations/`, and the private testsuite prompt's builder.

Done as: the section key `sonos` → `music` (PromptManager's table and order, the toolkit's `sections`,
the carried-section headings), the heading SONOS ZONES → MUSIC ZONES; `res/toolkits/music.md` speaks
of every speaker system — which ids play where (Spotify's anywhere, a system's own lists only in its
zones, a quick select only in its room) and what to tell the user when a HEOS speaker is not reachable
for the account; the toolkit summary, the `spotify_login` paragraph of `setup.md` (kept under the
toolkit's 10 000-character cap) and the `api_keys.spotify` comment no longer name Sonos alone. The
prompt changes for a Sonos house only by these texts. No private suites are linked on this machine,
so there was no testsuite prompt to rebuild; on a machine that has them, run their builder and the
prompt suites before the PR.

**Change events** (after Phase 4): `HeosEvents` listens on a connection of its own
(`system/register_for_change_events`, a heart beat after 30 s of silence, reconnects backing off 5 →
60 s). While it listens, HEOS caches each leader's status, refetches only the player an event names
and reports a change through `MusicSystem.zones_changed()` — only when the status line differs, since
HEOS sends most changes twice; a players or groups change drops the cached lists. `pushes_changes()`
is true while the stream is up; the zones section then wakes on a reported change and polls only every
60 s, and keeps its 3 s poll while any active system (Sonos) does not push. Live: play, pause and skip
from the apps reached the section within a second.

## Shortcut

Home Assistant has HEOS and Spotify integrations; extending Keep's `media_player` support (today state
and on/off only) with `media_player.play_media` / `select_source` would give basic control quickly, at
the cost of search and zone naming.

## Upstream

If it works, this goes to the Keep repository as a PR — only once the owner of this checkout says so.
