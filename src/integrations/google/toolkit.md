---
name: google_drive
summary: "search Google Drive and read a Google Doc or Slides deck page by page"
commands: [search_google_drive, read_google_doc, find_in_google_doc]
---
You can search the Google Drive of every signed-in Google account and read its Google Docs and Google Slides.
Read-only: nothing can be changed, created or shared.

`search_google_drive(words: str, account: str = "")` — the files whose name or content carries the words, newest
first, over every account or the named one: name, kind, account, date, owner and the id the read takes. A PDF, a
Sheet or an Office file turns up in a search but cannot be read here. When a search finds nothing, try fewer or other
words; a document mentioned in a mail or a chat is read straight from its link.

`read_google_doc(document: str, page: int = 1)` — the text of a Google Doc or a Slides deck, in numbered pages of about
5,000 characters. `document` is the docs.google.com or drive.google.com link, or the file id. The header of a page
says how many pages there are and gives the call for the next one. Do not read every page of a long document: find
the right one first. A deck is one slide after another, each under a `[slide N]` line with its speaker notes after it
as `Notes:`.

`find_in_google_doc(document: str, words: str)` — the lines of the document that carry all the words, each with the
page it is on (and the slide). Then read that page. Use a few distinctive words, not a sentence.

When you answer from a document, say which document and, for a deck, which slide.

### Stop-and-wait
`search_google_drive`, `read_google_doc`, `find_in_google_doc` return data: emit them and stop generating; the result
arrives in the next round.
