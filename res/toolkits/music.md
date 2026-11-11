You can control the speakers of the house — every speaker system together (Sonos, HEOS). The MUSIC data sent with
this reference lists each system's own lists and the user's own Spotify playlists, every entry with its id, and the
MUSIC ZONES under it the zones music can play in with what each plays now. A request that names no zone/room means
the room it came from (the location in its header), unless previous context suggests otherwise. Load this toolkit
again when you need the current state.

The commands:
`play(zone_name: str)`
`pause(zone_name: str)`
`next(zone_name: str)`
`volume_up(zone_name: str)`
`volume_down(zone_name: str)`

You can search for music on Spotify:
`search_music(search_query: str)`

This command searches songs, artists and public playlists simultaneously and returns the best results of each type.

Then you can start playing one of the search hits:
`play_music(zone_name: str, id: str)`

The `id` is one of the ids MUSIC or a search lists:
- A Spotify search hit: the short id from the search results (`AA`, `AB`, …). It plays in any zone.
- The user's own Spotify playlists (owned and followed): `S-0`, `S-1`, …. They play in any zone.
- A speaker system's own lists play only in that system's zones: the Sonos favorites (`F-0`, …) and Sonos playlists
  (`P-0`, …), the HEOS favorites (`H-0`, …). A room's quick select buttons (`Q-1`, …, listed under the room) play
  only in that room.

Each list holds at most 10 entries, in the order of the system's own app; the "S-" list is the newest 10 playlists of
the user's Spotify library. The section says when there are more, and search_music finds a public one by name.
Spotify-made playlists (Discover Weekly, Release Radar, Daily Mix) are not in the "S-" list — Spotify hides them from
apps like this one — so look for them among the favorites of the speaker system and, when they are not there, tell the
user to add the playlist as a favorite in that system's app. When the user asks for one of their own Spotify
playlists by name and no "S-" ids are listed, the Spotify account is not signed in yet: offer to sign it in with
`spotify_login` from the `setup` toolkit (a link to open, the landing address pasted back) — the playlists appear in
the section within a minute of the sign-in.

A HEOS speaker plays Spotify only while the household's Spotify account lists it among its devices: when
`play_music` answers that a speaker is not reachable for the account, tell the user to pick that speaker once in the
Spotify app; you can start music on it after that.

### Stop-and-wait
`search_music` returns data: emit it and stop generating; the result arrives in the next round.
