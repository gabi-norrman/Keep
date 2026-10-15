# Plan: the Google Docs feed — a doc's edits land in the history as diffs

Written 2026-10-15. Status: built the same day (`GoogleDocsIntegration.py`, `GoogleDrive.py`, `DocDiff.py`)
and run against the real document; the first run's lesson is the "What the first run taught" section.

## What we want

One very large Google Doc is edited a couple of times a week. Every editing session should land
in Keep's history as one row: who changed it, when, and the diff of what changed — so the
assistant knows what was added or rewritten without ever reading the whole document, and the
autonomous check or a watch item can act on it ("tell me when the trip section changes").

Not in this plan: a command that reads the doc, writes to it, or comments on it. The feed is
read-only, and the diff is the whole product.

## The design

**Source: the Drive revisions of the doc, never the document itself.** Drive API v3
`revisions.list` on a native Google Doc returns one revision per editing session (Google folds
the keystrokes of a session into one), each with `modifiedTime`, `lastModifyingUser` and
`exportLinks`, a per-format download of that revision. That is the Feed contract almost
verbatim: a cursor, items after it, a timestamp at the source, an author.

- **One diff per day edited** (one per revision was the first design — see "What the first run
  taught"), between the day's last revision's `text/plain` export and the text before it. The previous text is cached on disk, because Google merges and prunes
  old unnamed revisions: the cache, not an older revision, is the baseline.
- **Downtime costs nothing**: the server comes back a week later and emits one row per session
  that happened meanwhile, each dated and attributed, rather than one merged blob.
- **Polling is cheap**: every 10 minutes one `files.get` for `modifiedTime` per doc (a few
  hundred bytes). Only when that is newer than the cursor is the revision list fetched, and
  only a new revision is exported — a few MB a couple of times a week.

Rejected: polling `modifiedTime` and exporting the head (no author, sessions merge across
downtime, same cost); the Docs API `documents.get` (the whole structural JSON on every call,
no history); Drive push notifications (need a public HTTPS endpoint).

## Where it lives

`src/integrations/google/GoogleDocsIntegration.py`: `class GoogleDocsIntegration(Integration, Feed)`,
constant `name = "Google Docs"`, so `enabled_integrations` lists `Google Docs`. A second
Integration class in the google folder is found by discovery on its own
(`bootstrap.integration_classes` registers every Integration subclass of every module), and
importing `GoogleAuth` stays inside the folder — no allowlist grows. The Catalog tab lists it;
no tab of its own (the Google tab is the sign-in board already).

It needs the **Google** integration on: the sign-in (`google_login`) and the token files are
its. A doc feed with no token says so in `open_error`.

### Config

Per account, beside `mail` and `calendar`, in `src/integrations/google/config.example.yaml`:

```yaml
      # Google Docs whose edits land in the history (the "Google Docs" integration in enabled_integrations):
      # each editing session becomes one row with who changed what, as a diff. `id` is the part of the
      # doc's URL between /d/ and /edit; `name` is what the rows call the document.
      docs: []        # e.g. [{ name: Family plan, id: 1AbCdEf... }]
```

and in the root `config.example.yaml` under `feeds:`: `google_docs: { backfill_months: 1 }`.
The header comment of the google fragment gains: enable the **Google Drive API** in the Cloud
project; an account whose entry lists docs asks for the Drive read-only scope, and an account
signed in before docs were added must sign in again (`google_login`) to grant it.

### Scope

`SCOPE_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"` in `GoogleAuth.py`.
It is the narrowest scope that can export a revision (`drive.file` sees only files the app
created; `documents.readonly` is the Docs API, which has no revisions). It is a restricted
scope, the same class as the Gmail scope already in use, so nothing new in the consent screen.

`GoogleIntegration._build_accounts` is the one place that decides an account's scopes: a
helper `account_scopes(entry)` (mail → SCOPE_MAIL, calendar → SCOPE_CALENDAR, docs non-empty →
SCOPE_DRIVE_READONLY) replaces the inline lambda, and an account with docs but neither mail nor
calendar is kept (today it is skipped), with no mail or calendar account — its `check()` only
asks for a token. The docs feed builds its own `GoogleAuth` per account over the same token
file (`cache/oauth/google-<slug>.json` is the shared state; `token()` works from the file, and
a double refresh is harmless — Google keeps the refresh token valid).

The token file records the granted `scope` string. `GoogleAuth.has_scope(scope)` reads it, so
the feed tells "signed in without the Drive scope" before the first call and
`accounts_status()` says `sign in again — google_login("Name")` per account.

### The Drive client

`src/integrations/google/GoogleDrive.py`, the Drive calls over `requests` in the shape of
`GoogleCalendarAccount._call` (bearer token from `GoogleAuth.token()`, one retry on 429/5xx,
401 → `NotSignedIn`, 403 with reason `insufficientPermissions` → `NotSignedIn` with the
sign-in-again hint):

- `head(file_id)` → `files.get?fields=name,mimeType,modifiedTime`. The mimeType must be
  `application/vnd.google-apps.document`; anything else (a Sheet, a PDF, a wrong id) is logged
  once and the doc left out of the poll, never a feed failure.
- `revisions(file_id)` → `revisions.list?fields=nextPageToken,revisions(id,modifiedTime,lastModifyingUser/displayName,exportLinks)&pageSize=1000`,
  every page, oldest first as Google returns them.
- `export(revision)` → GET `exportLinks["text/plain"]` with the bearer token; `files.export?mimeType=text/plain`
  for the head when a revision has no links. The export cap is 10 MB; a doc beyond it is an error row, not a crash.

### The feed

- `feed_name = "Google Docs"` (the history `source`), `config_slug = "google_docs"`,
  `poll_interval = 600`, `default_backfill_months = 1`, `item_noun = "changes"`, `sensitive = True`.
- `open_feed`: at least one doc on an account that is signed in with the Drive scope; else
  `open_error` names the missing piece (no docs configured / not signed in / signed in without
  the scope, each with the `google_login` hint). Each doc's `head` runs here to learn its
  title and mimeType.
- **Cursor**: a JSON map `{file_id: <epoch of the last handled revision>}`. `initial_cursor`
  returns `{}`; a doc missing from the map (the first run, or a doc added to the config later)
  starts at `Feeds.horizon_for(self)`. `cursor_reached(cursor, target)` is overridden: every doc
  of `target` is at or past it in `cursor`.
- **fetch**: for each doc, `head`; when its `modifiedTime` is not past the doc's cursor, nothing
  to do (trace it). Otherwise list the revisions newer than the cursor and handle them oldest
  first: export, diff against the cached text, append a FeedEntry with the revision's
  `modifiedTime` as timestamp, the doc's name as `location`, then write the cache. One batch
  per poll with every doc's new revisions; `exhausted=True`; the cursor advanced per doc.
  `backfill` changes nothing (no side effects to skip).
- **Baseline, the first time a doc is seen** (no cached text): the newest revision *before*
  the horizon, when there is one, is exported as the baseline, so the catch-up yields real
  diffs for the whole backfill window. Without one, the oldest revision in the window becomes
  the baseline and its row says "now tracking «Name»: N paragraphs, M words" — never a diff of
  the entire document.
- **Cache**: `cache/google_docs/<file_id>.txt`, written atomically after the entry is built.
  It is a copy of the document, so `wipe_targets()` lists the folder.
- `check_health`: True while the runner is not FAILED and `open_feed` succeeded (the
  Telegram feed's pattern); `accounts_status()` per account: signed in with the scope, or the hint.

### The diff

`src/integrations/google/DocDiff.py`, pure functions, no I/O — the part to tune, and the part
the tests exercise most:

- Paragraphs: the export split on newlines, trimmed, blank lines dropped. A Google Doc exports
  one line per paragraph, so this is the natural grain.
- `difflib.SequenceMatcher` over the paragraph lists (`autojunk=False`). `insert` → `+ text`,
  `delete` → `- text`, `replace` → `-`/`+` pairs, each hunk preceded by one line of context
  (`  text`, cut to 120 chars) so the reader knows where in the document it happened.
- A 1:1 replace (one paragraph by one paragraph) with more than half its characters in common
  shows the paragraph once with inline marks `[-old-]{+new+}`; an edit of a word in a long
  paragraph must not print the paragraph twice.
- The row: `Google Doc «Name» edited by <displayName>: +3 −1 paragraphs` as the first line,
  the hunks under it, the whole capped at `DIFF_CHAR_LIMIT = 1900` — under the 2,000 characters a system row renders at in the prompt (`MessageHistory.MAX_RENDER_CHARS`), tail included — with a tail
  `… and N more changed paragraphs`. The history renders a feed row as `System (time): …`;
  the first line carries what the assistant needs even when the hunks are cut.
- A revision whose export equals the cache (formatting-only edits, a comment resolved) emits no
  row and still advances the cursor; `trace` says so.

### Logging

`info` on open (the docs and accounts), on every revision handled (doc, author, +/− counts,
chars exported), on a doc left out and why; `trace` on each poll with nothing to do and on
an unchanged export.

## What the first run taught (2026-10-15)

The document is 433,000 characters and Google exports it to text in about 26 s, whatever the
revision — the head too (measured: revision list 2.8 s, head export 27 s, revision export 25 s).
And it is not edited a couple of times a week: it is a training log two people write in daily,
64 revisions in a month. The first version exported every revision in one batch: 29 exports,
13 minutes, 0% on the console until the end, and a crash would have redone them all. So:

- **The unit of work is a day.** The revisions of one calendar day fold into one row, diffed at
  the day's last revision and naming every author ("Charlotte and Marcus (3 edits)"). One export
  per day edited instead of one per revision.
- **One step per fetch.** Each fetch does one day and answers `exhausted=False` with the count
  left; `remaining(cursor)` counts the steps ahead, so the catch-up shows progress and a
  restart resumes at the last export. The revision list is cached per head seen, so a plan
  and the fetch after it list once.
- **List markers are not compared.** The export numbers list items, so one block inserted near
  the top renumbered every numbered item after it and the first live row said "181 added, 166
  removed" for it. Paragraphs are compared with the number or bullet stripped and shown as they
  are; re-diffed with that, the same change is 16 added paragraphs in 678 characters.
- **An open session is held.** A doc whose head moved within the last poll interval is left
  for the next poll: one row per session instead of one per poll.

## Phases

1. **Scope and client**: `SCOPE_DRIVE_READONLY`, `account_scopes`, `GoogleAuth.has_scope`, the
   docs-only account kept, `GoogleDrive.py`. Config fragment and root `feeds:` entry.
2. **Diff**: `DocDiff.py` with its tests (the derivations: a changed word marks inline, a new
   paragraph is `+`, the cap keeps the first line and counts the rest, an unchanged text is None).
3. **Feed**: `GoogleDocsIntegration.py` — open, cursor, fetch, baseline, cache, health,
   `wipe_targets`. Tests over a fake Drive in `tests/unit/integrations/google/google_fakes.py`:
   two revisions give two rows with the revisions' timestamps and authors; an unchanged
   `modifiedTime` makes no revisions call; a first sight with a pre-horizon revision diffs
   against it, without one writes the "now tracking" row; the cursor map and `cursor_reached`;
   a missing scope is reported, not retried; a Sheet id is left out, the feed stays up.
4. **Wiring and docs**: `docs/atlas/descriptions.py` (the google folder blurb mentions Docs;
   the module docstrings open with one plain sentence), the readme's integration list, a `feeds`
   line in the google fragment's header. `test_integration_folders` allowlists untouched.

## Decided, so it is not reopened

- A change does not wake the assistant. It is a history row like a Telegram Feed message; the
  next turn and the autonomous check see it, and a watch item is the way to react to one.
- One feed for every doc of every account, not a feed per doc: one runner, one cursor map,
  one status line.
- The diff is paragraph-grained text. Formatting, images, tables' structure and comments are
  outside the text export and are not reported.
- The doc id, not its URL, is the config value; the comment says where in the URL it is.

## Open

- The inline-mark threshold (half the characters in common) and `DIFF_CHAR_LIMIT` are guesses
  to tune on the real document's first weeks of rows.
- A doc the first run finds with hundreds of revisions in the window exports each of them once;
  at a couple of sessions a week that is ten-odd exports of a few MB and fine. A doc edited
  daily by several people would want a per-day fold — not needed for this one.
