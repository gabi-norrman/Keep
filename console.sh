#!/bin/bash
# Open the console (the TUI) of a running server: ./console.sh [host]
# Defaults to the server on this machine; tui.py needs nothing beyond the standard library, so no venv.
cd "$(dirname "$0")"
exec python3 tui.py "${1:-localhost}"
