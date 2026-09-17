#!/bin/bash
cd "$(dirname "$0")"

RLOG=log/restart.log
rlog() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$RLOG"; }

rlog "start.sh invoked (caller-supplied old pid: ${1:-none})"

# Old server pids from three sources: the pid handed to us by the restarting
# server itself, the owners of the server's listen ports, and an argv match
# (anchored with $ to exclude testsuite instances). pgrep -f alone proved
# unreliable when start.sh is spawned from inside the server — it missed the
# running process and the replacement then died on a port collision.
old_pids="${1:-} $(lsof -t -iTCP:8888 -iTCP:43523 -sTCP:LISTEN 2>/dev/null) $(pgrep -f 'floxy_ai_server\.py$')"
old_pids=$(printf '%s\n' $old_pids | grep -E '^[0-9]+$' | sort -un)

if [ -n "$old_pids" ]; then
    rlog "stopping old server pid(s): $(echo $old_pids)"
    kill $old_pids 2>/dev/null
    # Wait until they're actually gone (up to 10s)
    alive=""
    for i in $(seq 1 20); do
        alive=""
        for p in $old_pids; do
            kill -0 "$p" 2>/dev/null && alive="$alive $p"
        done
        [ -z "$alive" ] && break
        sleep 0.5
    done
    if [ -n "$alive" ]; then
        rlog "survivor(s) after 10s, force-killing:$alive"
        kill -9 $alive 2>/dev/null
        sleep 1
    fi
else
    rlog "no old server process found — nothing to stop"
fi

# Never launch into occupied ports — the new server would spend ~25s loading
# models and then die on bind (Errno 48).
busy=""
for i in $(seq 1 10); do
    busy=$(lsof -t -iTCP:8888 -iTCP:43523 -sTCP:LISTEN 2>/dev/null)
    [ -z "$busy" ] && break
    sleep 0.5
done
if [ -n "$busy" ]; then
    rlog "ERROR: server port(s) still held by pid(s) $(echo $busy) — aborting launch"
    exit 1
fi

rlog "ports free — launching new server via Terminal"
# Remember where the log ends before the launch: the server can fail within
# milliseconds of starting (dependency check, bad config), and a `tail -n 0`
# attached after the sleep below would begin past those lines and show nothing.
log_offset=$(stat -f %z log/verbose-floxy.out 2>/dev/null || echo 0)
open -a Terminal "`pwd`/cli_start.sh"
sleep 0.5
osascript -e 'tell application "Terminal" to close (every window whose id is not (id of front window))' 2>/dev/null

# Only tail interactively — when spawned detached (auto-restart), a tail here
# would leak as an orphan process on every restart.
if [ -t 1 ]; then
    tail -c +$((log_offset + 1)) -f log/verbose-floxy.out
fi
