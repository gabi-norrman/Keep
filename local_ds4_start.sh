#!/bin/bash
# Starts the local ds4-server for Keep: DeepSeek V4.1 Flash (the IQ2_XXS/Q2_K imatrix GGUF) on
# this machine's GPU, served OpenAI-style on port 8000 on every interface, so a Keep on another
# machine of the local network reaches it as http://<this host>:8000/v1 (`ds4.base_url`).
# The ds4 checkout (github.com/antirez/ds4, built with make) is expected beside this repository
# as ../ds4; DS4_DIR points elsewhere.
#
# The disk KV cache flags matter: ds4-server keeps one live KV context and reuses it only
# for a prompt that extends it, so without them every switch between two prompts (another
# tier, the autonomous check, a Keep restart) prefills the whole prompt again — ~12 s for
# 16k tokens instead of 0.2 s. With them the server saves prompt prefixes to disk and loads
# the longest one that fits a request. (Keep's src/assistant/llm/Ds4LLM.py docstring has the
# measurements.) The budget is 64 GB because the server saves the evicted live state on every
# switch between prompts — ~253 MiB per entry at Keep's 40k-token prompt — and, once the budget
# is full, evicts the entry with no hits yet, which is the cold checkpoint a new prompt just
# stored: from then on every request of that prompt prefills the whole 40k from zero (31 s).
# 16 GB held ~60 switches; 64 GB holds ~250. Measured 2026-10-21 on the prompt test suite,
# where a reasoning effort other than none renders the history differently and is a new prompt.
#
# The server runs detached and appends to ds4-server.log in the ds4 folder (Keep's prompt tests
# read it for the real prompt and the cache decisions; a restart keeps the earlier runs' lines,
# so a problem seen before the restart can still be read). The script waits until the API answers,
# which takes a minute or two while the model is mapped. Stop it with `pkill ds4-server`.
#
# Overrides: DS4_DIR, MODEL (GGUF path), CTX (context tokens), KV_DIR, KV_MB, COLD_MAX, COLD_ANCHOR, HOST, PORT.
set -euo pipefail
DS4_DIR="${DS4_DIR:-$(cd "$(dirname "$0")/.." && pwd)/ds4}"
cd "$DS4_DIR" 2>/dev/null || { echo "no ds4 checkout at $DS4_DIR — clone github.com/antirez/ds4 beside this repository or set DS4_DIR"; exit 1; }

MODEL="${MODEL:-gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf}"
# 64k: the test-suite prompt is ~16.5k tokens and one round's command results (the server log
# tail alone is ~8.5k) took a test past 32k. The context buffers cost ~8 GiB per 32k on the
# M5 Ultra (the server's startup "memory:" line), so 64k is cheap next to the 152 GiB model.
CTX="${CTX:-65536}"
KV_DIR="${KV_DIR:-$HOME/.ds4/server-kv}"
KV_MB="${KV_MB:-65536}"
# The disk cache's "cold" checkpoint (the prompt cut at its first user turn, what a sibling
# request loads) is only written for a prompt up to this many tokens; the server's default
# of 30000 left the 40k prompt-test prompt with no reusable entry at all — every test
# prefilled from zero (32 s). With the ceiling at the context size a test loads the system
# prompt (11k) and prefills the rest (19 s). Measured 2026-10-21.
COLD_MAX="${COLD_MAX:-$CTX}"
# Where that checkpoint is cut: ds4's default is before the first task (the last user turn
# before the first assistant turn), which for Keep's prompt tests is the system prompt alone
# (11k of 40k). `last-user` cuts before the prompt's last user turn — the test's query, which
# the test server sends as a turn of its own on ds4 (Ds4LLM.prefers_query_as_own_turn) — so a
# test loads everything but its query from disk. Our ds4 branch kv-cold-anchor-last-user;
# an upstream ds4 without the flag refuses to start with it, so set COLD_ANCHOR= to drop it.
COLD_ANCHOR="${COLD_ANCHOR-last-user}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
LOG=ds4-server.log

[ -x ./ds4-server ] || { echo "no ds4-server binary in $DS4_DIR — run make there first"; exit 1; }
[ -f "$MODEL" ] || { echo "model not found: $MODEL (run ./download_model.sh)"; exit 1; }

busy=$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$busy" ]; then
    echo "port $PORT is already taken by pid(s) $busy — stop that server first (pkill ds4-server)"
    exit 1
fi

mkdir -p "$KV_DIR"

echo "starting ds4-server: $MODEL, ctx $CTX, disk KV cache $KV_DIR ($KV_MB MB), $HOST:$PORT"
nohup ./ds4-server \
    --model "$MODEL" \
    --ctx "$CTX" \
    --host "$HOST" \
    --port "$PORT" \
    --kv-disk-dir "$KV_DIR" \
    --kv-disk-space-mb "$KV_MB" \
    --kv-cache-cold-max-tokens "$COLD_MAX" \
    ${COLD_ANCHOR:+--kv-cache-cold-anchor "$COLD_ANCHOR"} \
    >> "$LOG" 2>&1 < /dev/null &
pid=$!
echo "pid $pid, log $PWD/$LOG"

# Mapping 340 GB takes a while; give up only if the process dies or 10 minutes pass.
for i in $(seq 1 600); do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "ds4-server exited during startup — the last lines of $LOG:"
        tail -n 20 "$LOG"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
        echo "ds4-server is up after ${i}s: $(curl -s "http://127.0.0.1:$PORT/v1/models")"
        echo "follow it with: tail -f $PWD/$LOG"
        exit 0
    fi
    sleep 1
done
echo "ds4-server did not answer on port $PORT within 10 minutes — it is still running as pid $pid; see $LOG"
exit 1
