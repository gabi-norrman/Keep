# Plan: read Google Docs and Slides — search Drive, read a document in pages, find inside it

Written 2026-11-04. Status: built the same day. Three deviations: (1) step 1 showed the plain-text
export of a deck runs the slides together, and the Slides API answers 403 `accessNotConfigured` until it
is enabled in the Cloud project — so a deck is read through the Slides API (`GoogleDrive.slides_text`,
`[slide N]` markers and the speaker notes as `Notes:`) and falls back to the export when the API is off,
with the page header saying what to enable; (2) `accounts_status` does not report a token without the
Drive scope — it would turn every account signed in before this into an error state while its mail and
calendar work; the first Drive command says it instead; (3) `read_google_doc` and `find_in_google_doc`
take no `account` argument — a file id is global and the read tries the accounts in order. Verified on
the production account: a search, a deck through the fallback, a 394k-character doc in 80 pages.

Sheets were added the same day, out of the "not in this plan" list: a spreadsheet is read through the
Sheets API (`GoogleDrive.sheets_text`, every tab under a `[sheet <title>]` line, a row per line with the
cells as shown, tab-separated) and falls back to the CSV export of the first tab — rendered the same way,
empty rows dropped — when that API is off, the header saying so. `find` knows the `[sheet …]` marker.

## What we want

The assistant can find a Google Doc or a Google Slides deck in the user's Drive and read it: "what does
the family plan say about the summer house", "read me the deck for Thursday", "find the budget line in
the board doc". Reading only, over the sign-in the Google integration already has (`google_login`, the
token files in `cache/oauth/`): no new OAuth client, no new flow, no new config key — the Drive scope
joins every sign-in. The Google Docs feed (`GoogleDocsIntegration`) keeps doing what it does — diffs of listed
documents — and is untouched except for sharing the Drive client.

Large documents are the problem, as with Dropbox: a command's result is kept whole up to
`MessageHistory.COMMAND_RESULT_CHARS` (6,000 characters) and cut after that, and a Google Doc can be
hundreds of thousands. So a document is read in **pages** of about 5,000 characters asked for one at
a time, and a **find inside the document** command says which page to open — the Dropbox shape,
same commands with Google names.

Not in this plan: PDFs, Word and PowerPoint files uploaded to Drive (they need a download and the
Dropbox extractor, a follow-up once the extractor is in core), Sheets, images, anything that writes,
a feed of Drive changes, a prompt section of recent files, a console tab.

## The design

**Three commands in the google folder, one toolkit.** `GoogleCommands` grows `search_google_drive`,
`read_google_doc` and `find_in_google_doc`; `src/integrations/google/toolkit.md` is a new toolkit
`google_drive` listing them (the login commands stay in `setup`, as today). It is offered while the
folder's integration is on (`Toolkit.offered_by` → `folder_enabled("google")`); an account that never
asked for Drive is reported by the command, not by the toolkit. `google_drive` takes its place in
`Toolkits.INDEX_ORDER` right after `dropbox`.

**No new config: every sign-in asks for the Drive scope.** `account_scopes` adds `SCOPE_DRIVE_READONLY`
unconditionally, beside mail and calendar, instead of only when docs are listed. An account signed in
before this has a token without the scope (unless it listed docs, which already brought the scope):
nothing of mail or calendar changes for it, and the first Drive command meets the 403 the client already
turns into `NotSignedIn` with `sign_in_again_hint` — the answer says to run `google_login` again.
`accounts_status` reports the missing scope with the same hint (as the docs feed's `_auth_problem` does),
so the Google tab and `list_integrations` show it before anyone asks. No Drive probe in the health check:
an install whose Cloud project has not enabled the Drive API must stay healthy for mail and calendar, and
the 403 `accessNotConfigured` on the first read says what to enable. A per-account `drive: false` opt-out
is a later key if an account ever needs one.

**`drive.readonly` is enough.** Drive `files.list` and `files/{id}/export` both run on it, and native
Docs and Slides both export as `text/plain` through Drive — no Docs or Slides API, no second API to
enable in the Cloud console beyond the Drive API the docs feed already asks for (the config comment
moves that sentence from "an account that lists docs" to every account). Whether the plain-text
export of a deck separates the slides is settled by step 1 against a real deck; if it runs them
together, the fallback is `presentations.get` of the Slides API (it accepts `drive.readonly`), walking
`slides[].pageElements[].shape.text` and emitting a `[slide N]` marker before each slide the way the
Dropbox `pptx_text` reader does — then `find` reports the slide, and the config comment says to enable
the Slides API too.

**The pages and the find move to core.** `DocumentText.pages`, `find`, `Hit`, `snippet` and their
constants are pure text functions that two folders now need, and a folder must not import another
(`tests/unit/test_integration_folders.py` — the allowlist shrinks, never grows). They move to
`src/assistant/integration/DocumentPages.py`; `DocumentText.py` keeps the extractors and imports the
rest from there, `DropboxCommands` imports from core. Their tests move with them to
`tests/unit/assistant/integration/test_document_pages.py`. Nothing changes in behaviour.

**A file is named by id or URL.** The assistant meets documents as links (in mail, in chat, in a
search result), so `read_google_doc` and `find_in_google_doc` take a URL or a bare id: `file_id_of(ref)`
in `GoogleDrive.py` cuts the id out of `docs.google.com/document/d/<id>/edit`,
`docs.google.com/presentation/d/<id>/…`, `drive.google.com/file/d/<id>/…`, `…?id=<id>` and leaves a bare
id alone. A file id is global, not per account, so a read tries the accounts in config order
and moves to the next on a 404 (the file is not shared with that account); a search
result carries the account it came from and the read may be told it (`account=`) to skip the trying.

### Where it lives

- `GoogleDrive.py` — the client, extended: `search(query, limit)` → `files.list` with
  `q = fullText contains '<words>' and trashed = false`, `fields = nextPageToken,files(id,name,mimeType,
  modifiedTime,owners/displayName)`, `orderBy = modifiedTime desc`, `pageSize = 20`; returns the entries
  and whether a `nextPageToken` came back. `head()` as it is; `export()` as it is — it already takes any
  file id and asks for `text/plain`, which Slides honour. `DocHead` gets `is_presentation` beside
  `is_document` and a `kind` word ("Google Doc", "Google Slides", else the mime type's tail) for the
  headers and the refusals. `file_id_of(ref)` as above.
- `GoogleDriveFiles.py` — new: the search over the accounts and the document read with its cache.
  `DriveFiles(accounts)` is built by `GoogleIntegration` over its accounts;
  `search(words, account="")` fans out (one account when named) and returns the matches tagged with the
  account; `document(ref, account="")` → `Document(account, head, text, from_cache)`: `head` first
  (one cheap `files.get`), a refusal for a type that is not a Doc or a deck, then the cache, then
  `export`. The cache is `cache/google_drive/<slug(id)>-<int(modified_ts)>.txt`: the modified time is
  in the name, so a hit is exact without a sidecar, every page of an unchanged document costs one
  `files.get` and no export (an export of a large doc is ~25 s, measured by the feed), and an edit gets
  a fresh export — older siblings of the id are deleted when the new one is written. `GoogleIntegration.
  wipe_targets()` lists the folder.
- `GoogleIntegration.py` — the Drive scope always in `account_scopes`, `files()` giving the `DriveFiles`
  over every account, and the scope verdict in `accounts_status`. The docstring's first paragraph names
  Drive.
- `GoogleCommands.py` — the three commands and their descriptions, `MULTI_PARAM_COMMANDS`, the
  `_integration` guard reused. The command bodies are `DropboxCommands._run_read` / `_run_find` /
  `_run_search` with Google's headers:

  ```
  «Family plan» — page 3 of 14 (68,000 characters; Google Doc of Personal, modified 2026-10-02).
  Next: read_google_doc("1AbC…", 4). Looking for something: find_in_google_doc("1AbC…", "<words>") says which page.
  ---
  <the page>
  ```

  A search answer lists `name  (Google Doc / Google Slides / PDF…, account, modified date, owner)` with the id
  on each line so the next call can use it, marks the kinds the reader cannot open, and ends with "More
  matches exist: narrow the words" when a next page token came back. An empty search says to try other
  words or the link from where the document was mentioned. A doc with no text, a page number out of
  range, a Sheet, a PDF: one sentence each, naming the kind.
- `toolkit.md` — `name: google_drive`, `summary: "search Google Drive and read a Google Doc or Slides
  deck page by page"`, `commands: [search_google_drive, read_google_doc, find_in_google_doc]`. The text
  is the Dropbox toolkit's shape: what can be read and what cannot, the page header gives the next call,
  do not read every page — find first, say which document you answer from (and the slide), the
  stop-and-wait list. Format-neutral per `docs/plans/FORMAT_NEUTRAL_TEXTS_PLAN.md`.
- `config.example.yaml` — no new key. The section comment says the Drive API must be enabled in the Cloud
  project for every account (today it says so for an account listing docs) and that an account signed
  in before the update signs in again with google_login to grant the Drive scope.
- `docs/atlas/descriptions.py` — the google blurb names the Drive search and the paged read.

### Logging

`info` on a search (the words, the accounts asked, the count), on an export (id, account, characters,
seconds) and on a refusal (the kind); `trace` on a cache hit, on a 404 moving to the next account and on
the page served. "Nothing to do" outcomes — no account with `drive` on, an empty search, a find with no
hit — are logged too.

## Tests (`tests/unit/integrations/google/`)

- `test_google_drive.py` — `search` sends the `q` with the words escaped (a quote in the words), reads
  every entry and the `nextPageToken` verdict; `file_id_of` on the four URL shapes and a bare id;
  `DocHead.kind` for a deck, a doc and a PDF.
- `test_google_drive_files.py` — a read of an unchanged document exports once over three pages (the
  cache file carries the modified time), an edit exports again and the older cache file is gone, a 404
  on the first account reads from the second, a Sheet is refused naming the kind, a named account is the
  only one asked. `google_fakes.py` grows a fake Drive answering `files.get`, `files.list` and the export.
- `test_google_commands.py` — the page header says the count and the next call, the last page gives no
  next call, a page out of range is refused, find lists the page (and the slide marker) of each hit,
  search lists the account and marks a kind the reader cannot open, a command on an account whose token
  lacks the scope answers with the sign-in-again hint.
- `test_google_integration.py` — `account_scopes` asks for Drive whatever the entry's flags; an account
  whose token lacks the scope is reported with the sign-in-again hint in `accounts_status`.
- `tests/unit/assistant/integration/test_document_pages.py` — the pages and find tests moved from
  `test_document_text.py`, unchanged.
- The existing suites that pin the prompt's shape (`test_texts_are_format_neutral`, the toolkit
  registry, the config schema fragments, the atlas) cover the new toolkit, the config comment and the
  blurb on their own.

## Steps

1. Export a real deck as `text/plain` through the existing `GoogleDrive.export` (the development
   machine's token already carries the Drive scope) — settle whether the slides come out
   delimited (then `[slide N]` markers are a regex over the export) or run together (then the Slides
   API fallback above goes into `GoogleDrive.py` as `slides_text(file_id)`).
2. Move the pages and the find to `assistant/integration/DocumentPages.py`; Dropbox green.
3. `GoogleDrive.search`, `file_id_of`, `DocHead.kind`; the scope in `account_scopes` and the status
   verdict in `GoogleIntegration`; the config comment.
4. `GoogleDriveFiles.py` with the cache; the three commands; the toolkit; `INDEX_ORDER`; the atlas.
5. Tests as above; the unit suite green. No prompt-suite run: the toolkit loads on demand and the
   template does not change.

## Open questions

- **Slides boundaries in the plain-text export** — step 1.
- **Shared drives.** `files.list` searches My Drive and what is shared with the account; a shared
  drive needs `includeItemsFromAllDrives=true&supportsAllDrives=true`. Cheap to add from the start if
  the household uses one; left off until someone misses a file.
