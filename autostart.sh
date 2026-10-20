#!/bin/bash
# The login item's script. "Keep Autostart.app" in this folder (common/Autostart.py builds it and
# registers it under System Settings > General > Login Items once the first-time setup sees Full
# Disk Access granted) runs it at login with no terminal attached. It starts the server the way it
# always starts — start.sh, through Terminal, so the Mac's grants apply — and opens no console;
# the Desktop shortcut (keep.sh) is for that. A server that is already up is left alone.
cd "$(dirname "$0")"

RLOG=log/restart.log
rlog() { mkdir -p log; echo "[$(date '+%Y-%m-%d %H:%M:%S')] autostart.sh: $*" >> "$RLOG"; }

if lsof -iTCP:8888 -sTCP:LISTEN >/dev/null 2>&1 || pgrep -f 'floxy_ai_server\.py$' >/dev/null 2>&1; then
    rlog "a server is already running — nothing to start"
    exit 0
fi
rlog "no server running — starting it"
exec ./start.sh
