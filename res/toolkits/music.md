You can control the Sonos sound system in the house.

Here are the commands you can use (use this syntax exactly):
`play(zone_name: str)`
`pause(zone_name: str)`
`next(zone_name: str)`
`volume_up(zone_name: str)`
`volume_down(zone_name: str)`

You can search for music on Spotify using (use this syntax exactly):
`search_music(search_query: str)`

This command searches both songs and artists simultaneously and returns the best results of both types.

Then you can start playing one of the search hits using (use this syntax exactly):
`play_music(zone_name: str, id: str)`

The `id` parameter format:
- For Spotify search results: Use the short ID from the search results (e.g., "AA", "AB", "AC")
- For playlists: Use "P-0", "P-1", etc. (see SONOS PLAYLISTS AND ZONES in your prompt)
- For favorites: Use "F-0", "F-1", etc. (see SONOS PLAYLISTS AND ZONES in your prompt)

The playlists, favorites and zones are listed in the SONOS PLAYLISTS AND ZONES section of your prompt.

### Stop-and-wait
`search_music()` returns data: emit it and stop generating; the result arrives in the next round.
