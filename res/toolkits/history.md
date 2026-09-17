In addition to your long term memory, you can search the raw history of everything that has ever happened — every
conversation, system event, photos, command, and notification. Use this when the user asks about something from the
past that isn't covered by your long term memory.

`search_history(keywords: str, sort: str = "relevance")`

Pass plain keywords — the system handles matching automatically. 
Make sure to use this EXTENSIVELY when a user mentions someone or something you do not have in your memory. Do NOT 
ask before you search, just search.

The `sort` parameter controls result ordering: `"relevance"` (default, best match), `"newest"` (most recent first),
`"oldest"` (earliest first).

To retrieve full conversation context around a specific time range (useful after search_history returns interesting
hits), use:

`show_history(from: YYYY-MM-DD HH:MM, to: YYYY-MM-DD HH:MM)`

Returns all messages chronologically within the given time window (up to 200 entries).

### Stop-and-wait
`search_history()`, `show_history()` return data: emit them and stop generating; the result arrives in the next round.
