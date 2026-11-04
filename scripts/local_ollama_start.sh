#!/bin/bash
cd "$(dirname "$0")/.."
mkdir -p log

stop_ollama() {
    # Match the server, the model runner subprocesses, and the macOS menu-bar app.
    local patterns=("ollama serve" "ollama runner" "ollama_llama_server" "Ollama.app")
    local found=0
    for pat in "${patterns[@]}"; do
        if pgrep -f "$pat" > /dev/null; then
            found=1
            echo "Stopping: $pat"
            pkill -f "$pat"
        fi
    done

    [ "$found" -eq 0 ] && { echo "No Ollama instances running."; return 0; }

    # Wait for graceful shutdown, then force-kill any stragglers.
    for _ in $(seq 1 25); do
        pgrep -f "ollama serve|ollama runner|ollama_llama_server" > /dev/null || break
        sleep 0.2
    done
    if pgrep -f "ollama serve|ollama runner|ollama_llama_server" > /dev/null; then
        echo "Force-killing remaining Ollama instances..."
        pkill -9 -f "ollama serve|ollama runner|ollama_llama_server"
    fi
    echo "All Ollama instances stopped."
}

if [ "$1" = "--stop" ]; then
    stop_ollama
    exit 0
fi

stop_ollama

echo "Starting Ollama with debug logging → log/ollama.log"
OLLAMA_HOST=0.0.0.0 OLLAMA_FLASH_ATTENTION=1 OLLAMA_DEBUG=1 OLLAMA_NUM_PARALLEL=1 ollama serve 2>&1 | tee log/ollama.log
