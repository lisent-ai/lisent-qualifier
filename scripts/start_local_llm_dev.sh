#!/usr/bin/env bash
# Start local LLM for DEVELOPMENT — any machine, no NUMA/taskset.
# Uses Ollama (OpenAI-compatible API on port 11434).
# For Hetzner AX102 (7950X3D) production use: start_local_llm.sh (llama.cpp native)

set -euo pipefail

MODEL="${MODEL:-qwen3.5:0.8b}"

if ! command -v ollama &>/dev/null; then
    echo "ollama not found."
    echo ""
    echo "Install:"
    echo "  pacman -S ollama          # Arch / CachyOS"
    echo "  brew install ollama       # macOS"
    echo "  curl -fsSL https://ollama.com/install.sh | sh  # Linux generic"
    exit 1
fi

# Pull model if not already present
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "Pulling $MODEL ..."
    ollama pull "$MODEL"
fi

echo "Starting Ollama (dev mode)"
echo "  Model:  $MODEL"
echo "  API:    http://localhost:11434/v1"
echo ""
echo "Set in .env:  LOCAL_LLM_URL=http://localhost:11434"
echo "              LOCAL_LLM_MODEL=$MODEL"
echo ""

exec ollama serve
