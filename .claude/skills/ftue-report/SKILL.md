---
name: ftue-report
description: Analyze a beta tester's first-time user experience (FTUE) from Keep's PostHog telemetry and write an HTML report with a summary, timeline, findings and the full chat transcript. Use whenever the user wants to understand how a tester's onboarding or setup went, asks to "look at the PostHog logs/events" for an install or distinct_id, wants a transcript of what a tester and the assistant said, or asks for a report on a beta user — even if they don't say "FTUE".
disable-model-invocation: false
allowed-tools: Bash(python3 *), Bash(security find-generic-password *), Bash(ls *), Bash(mkdir *), Read, Write
argument-hint: "<tester name> <distinct_id> [output path]"
---

Produce a self-contained HTML report on one beta tester's first-time user experience, from
their PostHog events. Argument: $ARGUMENTS (tester name, the install's distinct_id, optionally
where to put the report; default `~/Desktop/<Tester_Name>_FTUE_report.html`).

The reader is the developer. They want to *see what happened* without opening PostHog: a
verdict first, then the story, then the evidence. The transcript is the heart of the report
because the FTUE is a conversation; the findings exist to tell the developer what to fix.

## 1. Fetch the events

```bash
python3 -I .claude/skills/ftue-report/scripts/fetch_events.py <distinct_id> <workdir>/<distinct_id>
```

Use the session scratchpad as `<workdir>`. The script saves `events.json` and prints a digest:
every event in Swedish local time, the FTUE prompt shown once, and derived facts (sittings,
restarts, versions, integration switches, whether memory was ever curated, last human
message). Read the whole digest; it is usually 300–600 lines and *is* the story.

If the keychain item `posthog-personal2` is missing the script says so; ask the user to add a
read-capable PostHog personal API key under that name. Never print the key. Project, host and
event semantics are in `references/event-model.md` — read it the first time, and whenever a
property looks odd.

## 2. Work out what happened

Before writing, establish from the digest:

- **Where the tester got to** on the expected path (owner name → iMessage → iCloud → Gemini key
  → Photos → first memory), and the state of each step: done, done late, stuck, dropped, not
  started. "Done" needs evidence (an `integration_enabled` with state ok, the assistant
  confirming, a later heartbeat listing it); the assistant *saying* something is on is weaker
  than a heartbeat saying so.
- **How long each step took** in wall-clock time and in sittings (gaps over 30 minutes between
  human messages). Compare with the 15–20 minutes the FTUE prompt promises.
- **Where the tester went quiet**, and what the last thing asked of them was. That is usually
  the drop-off cause.
- **What the assistant got wrong**: contradictory instructions, wrong theories repeated across
  restarts, things it did not know about its own product, verbosity against a terse user,
  pitching side features (Self Improvement, integrations outside the path) to a day-one user.
- **What the product got wrong**: permission walls, feed errors in replies, restarts needed,
  history lost on restart (re-introduction), empty `feeds` in status, sensitive data in the
  transcript (2FA codes, phone numbers, usernames).
- **What went well**, honestly. A tester who keeps coming back is a strong signal; say so.

Severity: high = cost hours or caused the drop-off; medium = cost a sitting or confused the
tester; low = cosmetic or handled gracefully.

## 3. Write the analysis fragment

Write `<workdir>/analysis.html`: an HTML fragment (no `<html>`/`<body>`) using the template's
classes. Keep this exact section order so reports are comparable across testers:

```html
<h2 id="summary">Summary</h2>
<div class="card verdict">   <!-- 2 short paragraphs: verdict, then the one-line human story -->
<div class="tiles">          <!-- 4–6 .tile: time to each milestone, restarts, messages, steps done -->
<div class="card">           <!-- "Where they are now": table, one row per step, .pill ok/warn/bad -->
<h2 id="timeline">Timeline</h2>
<div class="card"><ul class="tl"> <!-- <h4>Day</h4> then <li class="ok|warn|bad"><span class="t">HH:MM</span>… -->
<h2 id="findings">Findings and recommendations</h2>
<div class="card finding sev-high|sev-med|sev-low"> <!-- <h3>N. Title <span class="pill bad|warn">sev</span></h3><p>what happened</p><div class="rec"><b>Do:</b> …</div> -->
<div class="card">           <!-- "What went well" list -->
```

Writing rules that make these reports useful:

- Lead the verdict with the outcome ("stalled at step 3 of 4, no value delivered yet"), then
  the numbers that justify it. Numbers in tiles, not in prose.
- Timeline entries are one line each, local time, from the tester's point of view. Quote the
  tester's own words in quotes; they are short and telling.
- Every finding has a concrete recommendation that names the mechanism (a preflight check, a
  command the assistant lacks, a prompt change), not "improve the UX".
- Mask secrets: never put a 2FA code, a phone number fragment or a macOS username in the
  analysis, even though the transcript may contain them. `build_report.py` masks six-digit
  messages in the transcript; mention the leak as a finding instead.
- Times in Swedish local time throughout (the scripts already convert), and say so once.

## 4. Build and check the report

```bash
python3 -I .claude/skills/ftue-report/scripts/build_report.py \
  --events <workdir>/<distinct_id>/events.json --analysis <workdir>/analysis.html \
  --tester "<Tester Name>" --out "~/Desktop/<Tester_Name>_FTUE_report.html" [--user <name they gave>] [--assistant <name>]
```

The script renders the transcript (user right, assistant left, proactive nudges tinted,
restarts and the injected FTUE prompt as collapsible grey events, tab browsing grouped into
one block, commands as chips) and the appendix (event counts, heartbeats, status table,
memory curations), and refuses to write if a placeholder is unfilled. Afterwards grep the
output for anything that must not be there (the 2FA code, `{{`) and skim the transcript
section once for broken markup. Then tell the user the path and the three or four things
that matter most, in plain words.

## Files

- `scripts/phq.py` — one HogQL query against project 294736 (key from keychain `posthog-personal2`)
- `scripts/fetch_events.py` — all events of a distinct_id → `events.json` + digest
- `scripts/build_report.py` — analysis fragment + events → final HTML
- `assets/template.html` — page shell and CSS (light/dark)
- `references/event-model.md` — event semantics and telemetry quirks
