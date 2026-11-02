# Plan: a restart the assistant started continues the conversation (issue #113)

Written 2026-11-02. Status: built the same day.

## The problem

On 2026-10-23 the owner asked for an update on Telegram. The assistant emitted `restart_server`
and promised, in the same reply, to check afterwards that the tiers were still on the right
models. The server was back in ten seconds and the fixed startup announcement ("Server is up
again — v2026.10.667. What's new …") reached the chat at once. The promised check came 37
minutes later, in the next periodic check, because nothing in the new process runs an LLM turn
after a restart. `try_pull_request` has the same gap: the user hears whether the trial's server
came up only at the next check or when they write again.

So the announcement itself is not the problem — the commit subjects and the release's what's
new already go out quietly within seconds of every start. The problem is a conversation left
hanging: the assistant restarted the server mid-dialogue and the dialogue does not resume.

## Design

Two kinds of start, two behaviours:

- **A nightly update, an audio recovery, a crash, a manual `start.sh`:** nobody is waiting.
  The fixed announcement goes out as today (quietly, in the history) and nothing else happens.
- **A restart the assistant started from a chat** (`restart_server`, `try_pull_request`): the
  announcement goes out as today, and then the owner's conversation is opened with a system
  turn so the assistant picks up where it left off — reports the outcome, keeps its promise.

### 1. The restart leaves a marker

`CodeQualityMaintainer.restart(reason, fallback_branch, resumes_conversation=False)` writes
`cache/resume_after_restart.json` (the reason and the time) right before it spawns `start.sh`,
when the flag is set. Only an actual spawn writes it, so a failed pull or pip sync leaves no
stale marker. `pull_and_restart` and `PullRequestTrial.start` pass the flag; the nightly update
and `AudioRecovery` do not.

### 2. On boot the marker opens the conversation

After `_announce_startup`, the launcher calls `StartupTracker.take_resume_marker()` (read and
delete). With a marker it opens the owner's conversation (`ChatHub.open_conversation`, the
turn the FTUE brief uses) with a short system turn: you restarted the server at `HH:MM`
(reason); it is up again on `Version.label()` after N seconds; the household was already sent
the startup announcement (which is in the history above); continue the conversation — report
the outcome, do what you said you would do. The trial and rollback notes are already in the
announcement and the system notes, so the turn does not repeat them.

`open_conversation` picks the attended owner chat, which is the one the user just approved the
restart in. When no owner chat is reachable it logs and drops the turn, as the brief does. With
the console running the turn waits for the startup intro like the brief (`console.after_intro`);
the brief wins when `brief_due()` — a first boot has no restart to resume.

### 3. Nothing else changes

The periodic check's cadence, the startup history row, `build_announcement` and the
announced-release bookkeeping stay as they are. The issue's workaround (a wake-up scheduled
before `restart_server`) becomes unnecessary; `describe_restart_server` need not change.

## Tests

- `tests/unit/common/test_startup_tracker.py`: the marker is taken once and absent afterwards;
  a missing or corrupt file is no marker.
- `tests/unit/assistant/agents/test_code_quality_maintainer.py`: `restart()` writes the marker
  only with the flag, and only when it spawns; `pull_and_restart` passes it; a failed pull
  leaves none.
- `tests/unit/test_launcher.py`: with a marker the owner conversation is opened after the
  announcement; without one it is not; with the console it waits for the intro; the brief
  takes precedence.

## Files

- `src/common/StartupTracker.py` — the marker read
- `src/assistant/agents/CodeQualityMaintainer.py` — the marker write in `restart()`, the flag
  through `pull_and_restart`
- `src/assistant/agents/PullRequestTrial.py` — `start()` passes the flag
- `floxy_ai_server.py` — `_resume_conversation(server)` after `_announce_startup`
