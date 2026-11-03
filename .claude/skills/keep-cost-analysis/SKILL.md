---
name: keep-cost-analysis
description: Analyse where this server's LLM spend went over a window of the log (the last 24 hours by default, the local log by default) and summarise where the cost is going, how it is distributed and what obvious driver could be optimised. Use whenever the user asks what has been costing money, why a day or a week was expensive, what the biggest cost driver is, whether something looks wasteful, or for a cost breakdown or cost analysis — even if they only say "look at the costs". For a what-if comparison of model setups use keep-cost-projection instead.
disable-model-invocation: false
allowed-tools: Bash(grep *), Bash(awk *), Bash(perl *), Bash(python3 *), Bash(ssh *), Bash(ls *), Bash(wc *), Bash(head *), Bash(tail *), Bash(date *), Read, Write
argument-hint: "[window, default 24h] [host, default local]"
---

Analyse the cost of running this server over a window of the log and answer three questions:
where is the cost going, how is it distributed, and is there anything obvious driving a lot of
cost that could be optimised. Argument: $ARGUMENTS — a window (`24h`, `3d`, `7d`; default the
last 24 hours) and optionally `prod`, which reads the production server's log over ssh
(`ssh 100.102.159.99`, same checkout path there) instead of the local `log/floxy.out`.

## What the log gives you

Read `log/floxy.out` in the repository root, not the `.log`: the `.out` has the thread column
that says what a call was for (a Telegram or console request, a periodic check, the memory
curation, the photo indexer, a feed). It is large and holds months — take the tail and
strip the ANSI codes before parsing, and keep the window honest when the server restarted.

Every Claude call logs its usage as a pair of TRACE lines on its thread: `Claude cache: … read,
… created (… 5m + … 1h), … uncached` and `Claude output: … tokens`. The model is in the nearest
`Sending to Claude (model)` or `Autonomous prompt … Claude (model)` line of the same thread, and a
call that came back through the Batches API follows a `Claude batch … answered` line and costs half.
The local backends (Ollama, the ds4-server) log their own metrics lines and cost nothing; other
paid services (xAI search, Gemini, voice) log calls but no price — count them, don't price them.

Price with the current rates from the claude-api skill, never from memory, and state the rates
you used. Work the model out per call from the log, not from today's config.

## The summary

Lead with the window, the host and the total. Then, in whatever shape the data calls for:

- **Where it goes** — by purpose (chat, autonomous checks, curation, photos, …) and by model,
  as money and as a share, with the counts behind it (requests, checks, runs) so the reader sees
  what the money bought.
- **How it is distributed** — by token type (uncached input, cache reads, 5-minute and 1-hour
  writes, output), and how concentrated it is: are a few calls most of the bill, or is it even?
- **What stands out** — the obvious drivers, named concretely (the dearest requests and checks
  and what they did, repeated work, prefix rewrites, writes nothing reads, unusually long
  outputs), each with what it cost and what fixing it would roughly save. Say plainly when
  nothing stands out.

Keep it to one screen: a table or two and a few lines of findings. Flag anything in the window
that will not recur (a one-off run, a restart, a bug since fixed) so nobody projects it forward.
