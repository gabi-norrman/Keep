#!/bin/bash
cd "$(dirname "$0")"

RLOG=log/restart.log
rlog() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$line" >> "$RLOG"
  # Dimmed on the terminal: it's bookkeeping, the server log that follows is what matters
  [ -t 1 ] && printf '\033[2;90m%s\033[0m\n' "$line" || echo "$line"
}

rlog "Start script invoked (caller-supplied old pid: ${1:-none})"

# Old server pids from three sources: the pid handed to us by the restarting
# server itself, the owners of the server's listen ports, and an argv match
# (anchored with $ to exclude testsuite instances). pgrep -f alone proved
# unreliable when start.sh is spawned from inside the server — it missed the
# running process and the replacement then died on a port collision.
old_pids="${1:-} $(lsof -t -iTCP:8888 -iTCP:43523 -sTCP:LISTEN 2>/dev/null) $(pgrep -f 'floxy_ai_server\.py$')"
old_pids=$(printf '%s\n' $old_pids | grep -E '^[0-9]+$' | sort -un)

if [ -n "$old_pids" ]; then
    rlog "Stopping old server pid(s): $(echo $old_pids)"
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

rlog "Ports free — launching new server via Terminal (so mic access works)..."
# Remember where the log ends before the launch: the server can fail within
# milliseconds of starting (dependency check, bad config), and a `tail -n 0`
# attached after the sleep below would begin past those lines and show nothing.
# On the very first start the log does not exist yet and tail would give up on it;
# the server opens it in append mode, so creating it empty here is harmless.
mkdir -p log && touch log/verbose-floxy.out
log_offset=$(/usr/bin/stat -f %z log/verbose-floxy.out 2>/dev/null || echo 0)  # BSD stat even with GNU coreutils on PATH
rm -f log/cli_start.tty
open -a Terminal "$PWD/scripts/cli_start.sh"
# cli_start.sh reports its tty once the server has left that window's session. Close
# that one window (or tab, if Terminal opened it as one) and nothing else of the
# user's; it would otherwise linger as "[Process completed]" after every restart.
for i in $(seq 1 40); do
    [ -s log/cli_start.tty ] && break
    sleep 0.25
done
if [ -s log/cli_start.tty ]; then
    rlog "closing the launch window on $(cat log/cli_start.tty)"
    osascript - "$(cat log/cli_start.tty)" >/dev/null 2>&1 <<'APPLESCRIPT'
on run argv
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is (item 1 of argv) then
                    if (count of tabs of w) is 1 then
                        close w
                    else
                        close t
                    end if
                end if
            end repeat
        end repeat
    end tell
end run
APPLESCRIPT
else
    rlog "cli_start.sh did not report its tty within 10s — leaving its window open"
fi

# A PR trial (src/assistant/agents/PullRequestTrial.py) hands over the branch to fall back to as $2:
# the server just launched runs a pull request's code, and if it is not listening within
# TRIAL_TIMEOUT seconds — or its process is gone before that — the previous branch is checked
# out again and the server relaunched on it, so a PR that cannot boot never leaves a dead server.
# The note left in log/ is what the relaunched server tells the user.
fallback="${2:-}"
if [ -n "$fallback" ]; then
    timeout="${TRIAL_TIMEOUT:-120}"
    rlog "PR trial: waiting up to ${timeout}s for the server to listen, falling back to $fallback otherwise"
    up=""
    for i in $(seq 1 "$timeout"); do
        [ -n "$(lsof -t -iTCP:8888 -sTCP:LISTEN 2>/dev/null)" ] && up=1 && break
        if [ "$i" -gt 15 ] && [ -z "$(pgrep -f 'floxy_ai_server\.py$')" ]; then
            rlog "PR trial: the server process is gone after ${i}s"
            break
        fi
        sleep 1
    done
    if [ -n "$up" ]; then
        rlog "PR trial: the server is up after ${i}s"
    else
        rlog "PR trial: the server did not come up — going back to $fallback"
        {
            echo "The server did not come up on the pull request's code within ${i}s, so it is back on $fallback. The last lines it wrote:"
            tail -n 30 log/raw-floxy.log 2>/dev/null || echo "(no output)"
        } > log/trial_rollback.txt
        if git checkout "$fallback" >> "$RLOG" 2>&1; then
            exec bash "$0"
        fi
        rlog "ERROR: git checkout $fallback failed — the server stays down on the pull request's code"
        exit 1
    fi
fi

# Only interactively — when spawned detached (auto-restart), a tail here would
# leak as an orphan process on every restart. Show the startup log until the
# server's TUI port is listening (at least 5s, at most 60s), then hand the
# terminal to the TUI; tui.py reconnects by itself if the server isn't up yet.
if [ -t 1 ]; then
    tail -c +$((log_offset + 1)) -f log/verbose-floxy.out &
    tail_pid=$!
    # A backgrounded job ignores SIGINT, so Ctrl-C would kill the script and
    # leave the tail running in the terminal — take it down on any exit.
    trap 'kill $tail_pid 2>/dev/null' EXIT
    trap 'rlog "interrupted — leaving the server running"; exit 130' INT TERM
    for i in $(seq 1 120); do
        [ "$i" -gt 10 ] && lsof -iTCP:5151 -sTCP:LISTEN >/dev/null 2>&1 && break
        sleep 0.5
    done
    kill $tail_pid 2>/dev/null
    rlog "handing the terminal to tui.py after ${i}x0.5s of tail"
    exec .venv/bin/python3 tui.py localhost
fi
