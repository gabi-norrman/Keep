#!/bin/bash
# The Desktop shortcut's script. Keep.app on the Desktop (common/DesktopShortcut.py builds it
# once the first-time setup sees Full Disk Access granted) runs it with no terminal attached,
# and it then opens one — Ghostty, else iTerm2, else Terminal — running this same script
# inside. With a terminal attached (that inner run, or ./keep.sh by hand) it attaches the
# console to the running server, or starts the server first: start.sh shows the startup log
# and hands the terminal to the console once the server is up. The server itself is launched
# the way it always is, by start.sh through Terminal, so the Mac's grants keep applying to it.
cd "$(dirname "$0")"

RLOG=log/restart.log
rlog() { mkdir -p log; echo "[$(date '+%Y-%m-%d %H:%M:%S')] keep.sh: $*" >> "$RLOG"; }

if [ ! -t 0 ]; then
    script="$PWD/keep.sh"
    # `open -Ra <app>` answers whether the app is installed without launching it.
    if open -Ra Ghostty 2>/dev/null; then
        rlog "opening the console in Ghostty"
        exec open -na Ghostty --args -e "$script"
    elif open -Ra iTerm 2>/dev/null; then
        rlog "opening the console in iTerm2"
        exec open -a iTerm "$script"
    fi
    rlog "opening the console in Terminal"
    exec open -a Terminal "$script"
fi

listening() { lsof -iTCP:8888 -sTCP:LISTEN >/dev/null 2>&1; }
process()   { pgrep -f 'floxy_ai_server\.py$' >/dev/null 2>&1; }

if listening; then
    rlog "server listening — attaching the console"
    exec python3 tui.py localhost
fi
# A process without a listener is booting (models load for ~25s) or shutting down (stop.sh,
# a restart): wait for it to listen or to go away, and only then attach or start.
if process; then
    rlog "server process without a listener — waiting for it to come up or go away"
    echo "A Keep server is starting or stopping — waiting for it..."
    for i in $(seq 1 120); do
        sleep 1
        if listening; then
            rlog "server up after ${i}s — attaching the console"
            exec python3 tui.py localhost
        fi
        process || break
    done
    if process; then
        rlog "server process still not listening after 120s — attaching the console anyway"
        exec python3 tui.py localhost
    fi
    rlog "server process went away after ${i}s"
fi
rlog "no server running — starting it"
exec ./start.sh
