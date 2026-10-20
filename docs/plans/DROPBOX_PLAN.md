# Plan: the Dropbox integration — search and read the files in Dropbox, read-only, in pages

Written 2026-10-20. Status: built the same day on the `worktree-dropbox` branch, not yet run against a
real account (steps 1 and 2 below are where that happens). One deviation: the login commands are
documented in the folder's `toolkit.md` under "Signing in", not in the `setup` toolkit, which sits at
its 10,000-character cap — `setup.md` carries a one-line pointer, as it does for WhatsApp.

## What we want

The assistant can find a file in the user's Dropbox and read it: "what does the lease say about
the deposit", "find the invoice from the roofer", "read me the minutes from last Tuesday". Reading
only — nothing is written, moved, shared or deleted, and the integration asks Dropbox for
read-only permissions so it cannot be talked into more.

Large documents are the real problem. A command's result is stored in the history whole up to
`MessageHistory.COMMAND_RESULT_CHARS` (6,000 characters) and cut after that, and a 90-page PDF
is a few hundred thousand characters. So a file is read in **pages** the assistant asks for one
at a time, and a **find inside the file** command tells it which page to open instead of walking
through all of them.

Not in this plan: a console tab (the sign-in works from the chat, as Google's does), a feed of
changes to watched files or folders (the Google Docs feed is the model when that comes), a
prompt section listing recent files, showing images or sending a file to the chat, spreadsheets,
and anything that writes. Each is a follow-up that fits inside the same folder.

## The design

**Raw REST over `requests`, no SDK.** The Dropbox API v2 is a handful of JSON POSTs
(`search_v2`, `list_folder`, `get_metadata`, `download`, `export`, `get_current_account`),
and the folder's client is written in the shape of `GoogleDrive.py` / `GoogleCalendarAccount._call`:
a bearer token from the auth object, one retry on 429 (honouring `Retry-After`) and 5xx, 401 →
`NotSignedIn` with the sign-in hint, 409 → the `error_summary` Dropbox sends
(`path/not_found/..`) turned into a sentence. `requests` is already pinned in the root file, so
the folder brings no `requirements.txt` — the `dropbox` package would add `stone` and `six` for
nothing we use.

**One account.** A household has one Dropbox; the config is a section, not an accounts list, and
the token lives in `cache/oauth/dropbox.json`. A second account is a later change to the section
shape, not something to design for now.

**Sign-in: the OAuth code flow with PKCE, loopback-by-paste.** Dropbox supports PKCE, so the
install needs only an app key — no secret in `config.yaml`, and the refresh goes through
`/oauth2/token` with `grant_type=refresh_token` and `client_id` alone. The flow is the one
`GoogleAuth` runs and `assistant/integration/OAuthPaste.py` shares: `begin()` builds
`https://www.dropbox.com/oauth2/authorize` with `response_type=code`,
`token_access_type=offline` (the refresh token), `code_challenge` / `code_challenge_method=S256`,
a random `state`, and the dead loopback redirect `http://localhost:18722/`; the user signs in,
lands on a page that never loads and pastes its address back; `finish()` checks the state and
exchanges the code with the verifier. Access tokens live four hours and `token()` refreshes
within a minute of the expiry; refresh tokens do not expire on their own.

Rejected: Dropbox's redirect-less flow, where Dropbox shows the code on a page for the user to
copy. It is one step shorter but has no `state`, needs a paste parser of its own, and gives the
user a second way to sign in that looks different from Google's. Same URL, same paste, same
instruction text wins.

**Scopes**: `account_info.read`, `files.metadata.read`, `files.content.read`. The app is created
at dropbox.com/developers/apps as a "Scoped access" app; its access type is the install's
choice — **Full Dropbox** to search everything, or **App folder** when the household wants the
assistant confined to a `/Apps/Keep` folder they drop things into (then every path the
assistant sees is relative to that folder, and nothing else exists for it). The config
comment explains both; the `folders` key below is the finer knife for the full-access case.

### Where it lives

`src/integrations/dropbox/`:

- `DropboxIntegration.py` — `class DropboxIntegration(Integration)`, constant `name = "Dropbox"`,
  `sensitive = True` (it is the user's documents), `config_keys = ["dropbox.app_key"]`,
  `login_hint = 'dropbox_login() from the chat'`. `check_health` is `users/get_current_account`:
  it proves the token, and the answer's `account_type` (`basic` / `pro` / `business`) is logged
  once — see "Search" for why. `wipe_targets()` lists the text cache; `log_out()` revokes the
  token at `auth/token/revoke` and deletes the token file. `login_begin()` / `login_finish()` /
  `sign_out()` mirror `GoogleIntegration`'s for the commands. The three docstring paragraphs the
  Catalog tab reads.
- `DropboxAuth.py` — the PKCE flow and the token file, `GoogleAuth` minus the client secret and
  the per-account naming; `NotSignedIn`, `not_signed_in_hint()`, `sign_in_again_hint()` as there.
  The pending flow is module state (the verifier and state outlive a `restart_integration`, the
  reason `GoogleAuth` keeps it so), one slot since there is one account.
- `DropboxClient.py` — the REST calls, metadata in, bytes out. `search(query, path, limit)`,
  `list_folder(path)`, `metadata(path)`, `download(path) -> bytes`, `export(path, "markdown")`
  for Paper docs, `current_account()`, `revoke()`.
- `DocumentText.py` — pure functions, no I/O: bytes and a name in, text out; then the paging and
  the find. The part the tests exercise most.
- `DropboxCommands.py` — the `CommandSet`: `dropbox_login`, `dropbox_login_code`,
  `search_dropbox`, `list_dropbox_folder`, `read_dropbox_file`, `find_in_dropbox_file`.
- `toolkit.md` — the reference, `name: dropbox`, pinned after `photos` in `Toolkits.INDEX_ORDER`.
- `config.example.yaml` — the `dropbox` section, with its `# >> dropbox: …` pointer line in the
  root example.
- A `GROUPS` blurb in `docs/atlas/descriptions.py`; tests in `tests/unit/integrations/dropbox/`.

Nothing is imported by name from core or another folder: discovery finds the integration, the
command set and the toolkit, so the allowlists of `test_integration_folders.py` do not grow.

### Config

```yaml
# Dropbox, the "Dropbox" integration: the assistant searches and reads the files of one Dropbox account,
# read-only. It needs an app of your own, once per install, at https://www.dropbox.com/developers/apps:
# Create app > Scoped access; choose "App folder" (the assistant only ever sees /Apps/<app name> — drop in
# what it may read) or "Full Dropbox" (everything, or the `folders` below); under Permissions tick
# account_info.read, files.metadata.read and files.content.read; under Settings add
# http://localhost:18722/ as a redirect URI and copy the App key here. No app secret is needed. Sign in
# with dropbox_login() from the chat: it gives a link to open in any browser; after the sign-in the
# browser lands on a http://localhost:18722/... page that does not load — that is expected — and that
# page's address is pasted back (dropbox_login_code). The token is kept in cache/oauth/. Text extracted
# from the files read is cached in cache/dropbox/.
dropbox:
  app_key: YOUR_APP_KEY
  # The folders the assistant may search and read, e.g. [/Documents, /Household]. Empty is the whole
  # Dropbox (or the app folder, for an App folder app). A path outside them is refused.
  folders: []
```

`folders` is read at call time (a `config_request` needs no rebuild); a search with no folder
given fans out over the listed roots, one `search_v2` per root, and a read or listing whose
path is under none of them is refused with the list.

### Search

`search_dropbox(query, folder="")` → `files/search_v2` with `options.path` (the folder, or each
configured root, or `""`), `max_results: 20`, `file_status: active`,
`match_field_options.include_highlights: true`. One result line per hit:

```
/Household/Lease 2024.pdf  (1.2 MB, 2024-03-02)  …the deposit of «two months'» rent…
/Household/Invoices/  (folder)
```

Only the first page of results is taken — `has_more` becomes a trailing "more matches: narrow
the query or give a folder" line, never a `search/continue_v2` cursor the LLM would have to carry.

Whether Dropbox searches *inside* files depends on the account's plan — content search is a
paid-plan feature; on a plan without it `search_v2` answers with filename matches alone and
says nothing about it. (Which plans have it is not settled by the API reference; the forum
threads say "certain plans" — the first run against the real account settles it.) So:
`check_health` logs the `account_type`, the toolkit tells the LLM that a search may be by name
only, and `find_in_dropbox_file` is the content search that always works, one file at a time.
`list_dropbox_folder(path)` (`files/list_folder`, one page of up to 200 entries, folders first)
is for when the name is unknown and the place is known.

### Reading, in pages

`read_dropbox_file(path, page=1)`:

1. `get_metadata(path)` for the `size`, `content_hash` and `.tag`. A folder, a file over
   `DOWNLOAD_MAX_BYTES` (50 MB) and a type the extractor has no reader for are refused with one
   sentence that says what it is and how big.
2. The text cache: `cache/dropbox/<content_hash>.txt`. Dropbox's `content_hash` is a hash of the
   bytes, so a hit is exact — the same file is downloaded and extracted once, however many pages
   are read, and an edited file gets a new hash and a fresh extraction. A miss downloads
   (`files/download`, the `Dropbox-API-Arg` header carries the path) or exports (a Paper doc,
   `files/export` as Markdown), extracts, and writes the text atomically.
3. `DocumentText.pages(text)` cuts the text into pages of at most `PAGE_CHARS = 5,000` characters
   at paragraph boundaries (a blank line, else a line end, else hard), so a page plus its header
   stays under the 6,000-character cap and the history keeps it whole.
4. The result is the header, then the page:

   ```
   «Lease 2024.pdf» — page 3 of 14 (68,000 characters; 1.2 MB PDF, modified 2024-03-02).
   Next: read_dropbox_file("/Household/Lease 2024.pdf", 4). Looking for something: find_in_dropbox_file(path, words).
   ---
   [p. 7] … the text …
   ```

   A page out of range says how many there are. A file that fits in one page has no "next".

The extractor, by extension (the mime type Dropbox sends is not reliable for a `.md`):

- text-like (`.txt .md .csv .json .yaml .log`, code, anything that decodes as UTF-8 and has no
  NUL bytes): the bytes decoded, UTF-8 then Latin-1.
- `.pdf`: `pdfplumber` (already pinned at the root), one `[p. N]` marker before each PDF page so
  the LLM can cite "page 7 of the PDF" and the find answers in PDF pages too. A scanned PDF
  extracts to nothing and the reply says so (no OCR in this plan).
- `.docx`: `zipfile` + `xml.etree` over `word/document.xml`, one line per `w:p` paragraph,
  tables row by row with tabs — stdlib, no pin. `.pptx` the same way over the slide files, a
  `[slide N]` marker each. (`.doc` and `.pages` are refused as binary.)
- `.paper`: `files/export` with `export_format: markdown`.
- `.xlsx`, images, audio, video, archives: refused with the type and size. Spreadsheets are a
  follow-up (`openpyxl` is not pinned); images belong with a show command, not a read.

Extraction runs on the IO lane with the command (`background_command`), and `pdfplumber` takes
a few hundred milliseconds a page — a 300-page PDF is a minute the first time. The reply of a
read that took long still arrives as the command's result; the log says how long the
extraction took and how many characters it gave, and the cache makes the second page instant.

`find_in_dropbox_file(path, words)` runs the same extraction (or cache hit) and answers with
up to 20 matching lines, each with its page number and, for a PDF, its PDF page:

```
«Lease 2024.pdf» — 4 lines match "deposit":
page 3 [p. 7]: … a deposit of two months' rent is payable on …
page 11 [p. 31]: … the deposit is returned within 30 days …
```

The match is case-insensitive on each word, every word on the line (the `words` are a few
terms, not a sentence; the toolkit says so). This is what makes a 14-page read a one-page read.

The cache is a copy of the user's documents: `wipe_targets()` lists `cache/dropbox/`, and the
folder is kept under `CACHE_MAX_BYTES` (200 MB) by deleting the oldest-touched files when a
write would pass it (`info` says what went).

### The toolkit

`src/integrations/dropbox/toolkit.md`, offered while the integration is on:

```
---
name: dropbox
summary: "search the files in Dropbox and read a document, page by page"
commands: [search_dropbox, list_dropbox_folder, read_dropbox_file, find_in_dropbox_file, dropbox_login, dropbox_login_code]
---
```

The reference says: search first (`search_dropbox(words, folder)` — a search may match names
only, depending on the plan, so look inside a likely file with `find_in_dropbox_file`), then
read; a document comes in numbered pages and a page's header says how to get the next one —
read the page the find points at, not every page; cite the document and its page when you
answer from it; read-only, nothing can be changed or shared; a "Signing in" section with
`dropbox_login` / `dropbox_login_code` (the `setup` toolkit points here); and the Stop-and-wait
block every data toolkit ends with.

`MULTI_PARAM_COMMANDS` = the three that take a second argument (`search_dropbox`,
`read_dropbox_file`, `find_in_dropbox_file`); nothing is sensitive (nothing sends or changes),
nothing needs approval.

### Logging

`info` on each sign-in step, on every search (query, folder, hits, whether `has_more`), on a
download (path, bytes, extractor, characters, seconds) and a cache hit, on a refusal and why
(outside the folders, too big, no reader, not found); `trace` on a page served (which of how
many), on a find with no match, and on the health check's account type.

## Tests (`tests/unit/integrations/dropbox/`)

A `FakeSession` in the folder's fakes, the shape of the X and Google ones, answering the
endpoints by URL. Nothing touches the network or the real cache (`tmp_path` and a patched
`TEXT_CACHE_DIR`).

- `test_dropbox_auth.py`: the authorization URL carries the S256 challenge of the verifier,
  offline access and the state; a pasted address with the wrong state is refused and no
  exchange is made; the exchange sends the verifier and no secret; the refresh sends the client
  id and no secret; `invalid_grant` marks the account as needing a new sign-in and keeps the
  file; a restart between begin and finish keeps the flow (module state).
- `test_dropbox_client.py`: 401 → `NotSignedIn` with the hint; 409 `path/not_found` → a
  sentence naming the path; 429 with `Retry-After` retried once; a search request carries the
  path option, the active-only filter and the highlights flag.
- `test_document_text.py`: a UTF-8 and a Latin-1 text; a two-page PDF fixture
  (`tests/unit/data/dropbox/two_pages.pdf`, synthetic, checked in) gives two `[p. N]` markers in
  order; a `.docx` built in the test with `zipfile` gives its paragraphs and a table row; a
  scanned PDF (no text layer) extracts to empty; paging cuts at a paragraph boundary, never
  mid-word, every page under the cap, the pages concatenate back to the text; a find reports the
  page and the PDF page of each hit and caps at 20.
- `test_dropbox_commands.py`: a read's result stays under `COMMAND_RESULT_CHARS` for a file of
  60,000 characters; page 2 of the same file makes no second download (one `download` call on
  the fake session); an edited file (new `content_hash`) downloads again; a path outside
  `folders` is refused and names the roots; a 60 MB file is refused and says its size; a page
  past the end says how many pages there are; `has_more` adds the narrowing hint; the login
  command's reply carries the URL and, when no chat asked, a system note.
- `test_dropbox_integration.py`: `check_health` calls `get_current_account` and logs the
  account type; `NotSignedIn` surfaces as the health error with the hint; `wipe_targets` names
  the cache folder; `log_out` revokes and deletes.

Fictional data throughout: paths like `/Household/Lease 2024.pdf`, the cast's names, no real
Dropbox content.

## Steps

1. `DropboxAuth.py` + `DropboxIntegration.py` + the config fragment and pointer line;
   `dropbox_login` / `dropbox_login_code` in `DropboxCommands.py`. Sign in against the real
   account; `list_integrations` shows it healthy, and the log shows the account type.
2. `DropboxClient.py` + `search_dropbox` / `list_dropbox_folder`; the toolkit with those two.
   Find out whether the real account's search matches content.
3. `DocumentText.py` (text, PDF, docx, Paper) + the cache + `read_dropbox_file` with paging.
4. `find_in_dropbox_file`; the toolkit text completed; the atlas blurb; the `setup` toolkit's
   login list.
5. Tests as above; the unit suite green; a prompt-suite run is not needed (the toolkit is
   loaded on demand and the template does not change).

## Open questions

- **Content search on the real plan.** Settled by step 2. If the plan has none, the toolkit's
  wording leans harder on `find_in_dropbox_file`, and a later follow-up could index the text
  cache locally.
- **App folder or full access** for the first install. The plan supports both; the config
  comment recommends App folder for a household that wants a clear boundary.

## References

- Dropbox search API: https://dropbox.tech/developers/search-in-dropbox-api-v2 and
  https://fast.io/resources/dropbox-search-api/ (plan gating of content search unresolved there)
- PKCE and refresh without the secret: https://www.dropboxforum.com/discussions/101000014/using-refresh-token-without-client-secret/584036
- Short-lived tokens and `token_access_type=offline`: https://dropbox-sdk-python.readthedocs.io/en/latest/api/oauth.html
- In this repository: `docs/plans/GOOGLE_DOCS_FEED_PLAN.md` (the folder and feed model),
  `src/integrations/google/GoogleAuth.py` and `assistant/integration/OAuthPaste.py` (the sign-in),
  `MessageHistory.COMMAND_RESULT_CHARS` (what fixes the page size).
