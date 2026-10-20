---
name: llm-cost
description: Estimate the monthly Claude API cost of this server under the five model setups (Opus or Fable on chat, Opus or Fable on memory curation, and the prepended user query on Opus or on Fable) from the last days of the local log, and present it as one comparison table. Use when the user asks what the assistant costs to run, what switching a tier between Opus and Fable would save or add, or for a cost table or cost report.
disable-model-invocation: false
allowed-tools: Bash(grep *), Bash(awk *), Bash(perl *), Bash(python3 *), Bash(ls *), Bash(wc *), Bash(head *), Bash(tail *), Read, Write
argument-hint: "[days, default 7]"
---

Estimate what this server's Claude usage costs per month under five setups, from the last
N days of `log/floxy.out` in the repository root. Argument: $ARGUMENTS — the number of days to
analyse, default 7. Gemini, voice and the photo-indexing calls are out of scope.

## What to measure

Every Claude call logs two TRACE lines, `Claude cache: … read, … created (… 5m + … 1h), …
uncached` and `Claude output: … tokens`. The `.out` file has a thread column that says what the
call was for — use it, the plain `.log` does not have it. Three kinds of call matter:

- **Chat** — the request threads (Telegram, console). The model is in the `Sending to Claude
  (model)` line. A request starts at its "byte-prefix tail" line and usually runs several rounds.
- **Autonomous checks** — the periodic check, due watcher and delayed check threads. They log no
  "Sending" line; the model is in the "Autonomous prompt … Claude (model)" line. They follow the
  chat model, so count them on the chat side of the table but show them in their own column.
- **Memory curation** — the curation and condensing threads. The nightly run goes through the
  Batch API: its usage lines follow the "batch … answered" line and are billed at half price.
  A curation run by hand (`//memory_curate`) is direct and not batch; keep it out of the
  recurring estimate and mention it separately. Normalise the nightly runs to one per night.

Work out which model each call ran on from the log, never from the current config — a tier may
have switched mid-window. Price the same token volumes under each setup; the tokenizers match.

## Pricing

Take the current rates from the claude-api skill, never from memory: uncached input, output,
cache read, and the 5-minute and 1-hour cache write rates for each model, the batch discount, and
whether a long-context tier applies above some input threshold (none on Opus 5.5 and Fable 5.1 as
of October 2026 — the 1M window is standard-priced; check again, the nightly curation's 280k
input would land in one). State the rates used under the table.

The prepended-user-query setup cannot be read from the log while it is off: model it. With it
on, every chat request writes its whole prompt at the 5-minute write rate and only its own
follow-up rounds read it back; nothing is shared between requests. Price that modelled volume
on both models — the table has a prepended row for Opus chat and one for Fable. Leave the autonomous checks
as measured and say that they would likely cost more too, since chat no longer keeps the shared
prefix warm.

## The table

Scale the window to a month (365 / 12 days). One row per setup, in this order, with the chat,
autonomous-check and nightly-curation columns, the bold monthly total, and a "Step up" column
giving each row's increase over the row above (the first row shows a dash). Each of the three
cost columns carries a count in parentheses after the dollar figure so the reader sees what the
money bought: chat and autonomous checks as the average per day over the window, written
`(n/day)` — chat counted in requests, a message the user sent, and autonomous checks counted
once per check, never in the model rounds either of them runs (a check that runs commands goes
back to the model once per result; about half answer in one round) — and curation as the number
of nightly runs in the month, written `(n runs)`:

| Setup | Chat | Autonomous checks | Nightly curation | **Month** | Step up |
|---|---|---|---|---|---|
| Opus chat + Opus curation | $n (n/day) | $n (n/day) | $n (n runs) | **$n** | – |
| Opus chat + Fable curation | | | | | +n% |
| Opus chat + prepended user query + Fable curation | | | | | +n% |
| Fable chat + Fable curation | | | | | +n% |
| Fable both + prepended user query | | | | | +n% |

Under the table, in a few lines: what the window actually ran (which model on which tier, and
when it switched), the measured cost of the window as it was, the volumes (requests, rounds,
checks, curations), why the gaps are the size they are, and the caveats — anything in the window
that will not recur (a cache bug since fixed, a hand-run curation, an unusually heavy day) and how
much of the estimate it accounts for. If the log shows a different output-token rate per model,
say what that is worth. Keep it to the table and those few lines.
