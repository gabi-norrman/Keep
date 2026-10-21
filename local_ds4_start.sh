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
# the longest one that fits a request. One saved entry is ~111 MiB on Flash, so 16 GB holds
# about 140 of them. (Keep's src/assistant/llm/Ds4LLM.py docstring has the measurements.)
#
# The server runs detached and writes ds4-server.log in the ds4 folder (Keep's prompt tests read
# it for the real prompt and the cache decisions). The script waits until the API answers,
# which takes a minute or two while the model is mapped. Stop it with `pkill ds4-server`.
#
# Overrides: DS4_DIR, MODEL (GGUF path), CTX (context tokens), KV_DIR, KV_MB, HOST, PORT.
set -euo pipefail
DS4_DIR="${DS4_DIR:-$(cd "$(dirname "$0")/.." && pwd)/ds4}"
cd "$DS4_DIR" 2>/dev/null || { echo "no ds4 checkout at $DS4_DIR — clone github.com/antirez/ds4 beside this repository or set DS4_DIR"; exit 1; }

MODEL="${MODEL:-gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf}"
CTX="${CTX:-32768}"
KV_DIR="${KV_DIR:-$HOME/.ds4/server-kv}"
KV_MB="${KV_MB:-16384}"
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
# Keep the previous run's log for one more run; the server overwrites the live one.
[ -f "$LOG" ] && mv -f "$LOG" "$LOG.1"

echo "starting ds4-server: $MODEL, ctx $CTX, disk KV cache $KV_DIR ($KV_MB MB), $HOST:$PORT"
nohup ./ds4-server \
    --model "$MODEL" \
    --ctx "$CTX" \
    --host "$HOST" \
    --port "$PORT" \
    --kv-disk-dir "$KV_DIR" \
    --kv-disk-space-mb "$KV_MB" \
    > "$LOG" 2>&1 < /dev/null &
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
