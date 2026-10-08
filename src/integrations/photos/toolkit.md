---
name: photos
summary: "search the family photo library and show photos or videos"
commands: [search_photos, show_photo]
---
You have access to all the families photos. You have a searchable database of {PHOTO_COUNT} indexed photos and videos at your disposal:

`search_photos(keywords: str, sort_by: str = "relevance")`

This will give you a list of top hits for the keywords (use keyword * for all photos). You can use normal keywords (big restaurant, green car,
view of alps, etc) but also use keywords for persons in the photo (alex, emma, etc) and locations (home, France,
Frankfurt, etc). The "pretty" and "family" numbers in the search result are scores for how pretty the picture is and
how much of a family picture it is. Videos are tagged `(video, 12s)` (also `slo-mo` / `time-lapse` / `cinematic`)
with their length; a video's description covers what happens in the clip and what is said in it.

The `sort_by` parameter controls result ordering: `"relevance"` (default, best keyword match), `"pretty"` (most
beautiful photos first), `"family"` (most important family photos first), or `"best"` (combined pretty+family score).
Don't talk about the family/pretty scores with the user - that's too technical.

To show a photo or video to the user (sent to the chat, or shown on the house's screen when the request was spoken and the install has one), use the
photo ID from search results:

`show_photo(photo_id: int or comma-separated ints)`

You can show multiple photos at once: `show_photo("42", "57", "103")`. Up to 4 photos at a time looks best.

### Stop-and-wait
`search_photos()` returns data: emit it and stop generating; the result arrives in the next round.
