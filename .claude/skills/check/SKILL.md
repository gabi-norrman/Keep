---
name: check
description: Review latest changes using several agents, curate and create summary
disable-model-invocation: false
allowed-tools: Bash(git *), Bash(node *), Bash(ls *), Task
---

Run a Claude sub-agent and a Codex subagent. Have them review the changes in working tree (or the latest commit if no working tree changes).
Once they’re all done, review their findings, do your own research to rule out false positives and minor bugs, and write a final report.

## Determine the review target

**If an argument was passed** (e.g. `last 24h`, `last 3 commits`, a base ref like `abc123`, or a range), use it as the target. Resolve it to a concrete base ref:

- A time window (`last 24h`, `since yesterday`) → `git log --since="..." --format=%H | tail -1` gives the oldest commit in the window; its parent is the base. Review `<base>..HEAD`.
- `last N commits` → base is `HEAD~N`.
- An explicit ref or range → use it directly.

Pass that base to Codex as `--base <ref> --scope branch`, and give the Claude sub-agent the same `<base>..HEAD` diff.

**If no argument was passed**, auto-detect:

```bash
git status --short
```

- If there are uncommitted changes → review the **working tree**.
- If the tree is clean → review the **latest commit** (`HEAD`, i.e. `HEAD~1..HEAD`).

Give both reviewers the same target and the same context (the diff plus the implementation the change touches, not just the diff in isolation).

## Run both reviewers in parallel

The two reviewers are independent — launch them **concurrently**, don't wait for one before starting the other. The harness runs independent tool calls in parallel **only when they're issued in the same assistant message**, so:

- Emit the `Task` call (Claude sub-agent) and the `Bash` call (Codex `review --wait`) **together in a single response** — one message, two tool calls.
- The Codex `--wait` is fine: it blocks only *its own* Bash call, not the sibling `Task`. Both run at the same time and both results come back in the same turn.
- Do **not** split them across two messages (Task first, then Bash, or vice-versa) — that serializes them and defeats the point.

## How to start the Claude sub-agent

Use the `Task`/`Agent` tool with `subagent_type: general-purpose`. Put the concrete target and instructions in the prompt: have it read the diff, find and read the implementation the change exercises, 
RUN any relevant tests, and report findings as a severity-tagged list (critical/major/minor/nit) with `file:line` references. Tell it to distinguish real bugs from stylistic nits and to say so plainly
if it finds nothing.

## How to start the Codex sub-agent

Do **not** use the `codex:codex-rescue` agent for this — that agent only forwards to Codex `task` (a write-capable implementation run), not a review. Use the Codex companion's `review` command 
directly via Bash.

Locate the companion script version-agnostically (the path contains a version number that changes):

```bash
ls ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs
```

Then run the review in the foreground with `--wait` so the result comes back synchronously:

- **Working-tree changes** (default scope reviews the working tree):
  ```bash
  node "<companion-script-path>" review --wait
  ```
- **Latest commit only**:
  ```bash
  node "<companion-script-path>" review --wait --base HEAD~1 --scope branch
  ```

The command prints a `# Codex Review` section to stdout — that is Codex's findings; use it directly.

**Caveat:** `--scope branch --base HEAD~1` diffs `HEAD~1..HEAD` **plus any uncommitted working-tree changes**. If the tree is dirty, Codex may flag files unrelated to the commit (e.g. local settings).
Check `git status` and discount findings on files that aren't part of the target.

## Final report

Compare both reviewers' findings. Independently verify the key claims (re-read the cited code, re-run the cited tests) to rule out false positives. Then write a concise final report: the target 
reviewed, each reviewer's verdict, which findings survived your own scrutiny, and a clear ship / don't-ship conclusion. We're not interested in small nit-pick issues, we're looking for actual
problems.

Keep the report short. Create three tables: one with results from claude, one with results from codex and one with what you actually feel needs to be fixed. Every row of every table carries the finding's severity (critical/major/minor/nit) in a column of its own — an automated fix step reads the last table and takes on the critical and major rows.
