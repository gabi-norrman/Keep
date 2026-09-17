#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python3 floxy_ai_server.py < /dev/null >> log/raw-floxy.log 2>&1 &
