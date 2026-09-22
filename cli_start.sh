#!/bin/bash
# Runs inside the Terminal window that start.sh opens: Terminal is then the process macOS
# holds responsible for the server, so its microphone / Photos / Full Disk Access grants
# apply. The server leaves the window's session (setsid) before it starts, so start.sh can
# close the window without Terminal asking about running processes and without hanging
# up the server; the tty is reported only once that has happened.
cd "$(dirname "$0")"
source .venv/bin/activate
python3 -c 'import os, sys; os.setsid(); open(sys.argv[1], "w").write(sys.argv[2]); os.execv(sys.executable, [sys.executable] + sys.argv[3:])' \
    log/cli_start.tty "$(tty)" floxy_ai_server.py < /dev/null >> log/raw-floxy.log 2>&1 &
