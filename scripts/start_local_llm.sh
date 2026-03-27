#!/usr/bin/env bash
# Start llama.cpp server with NUMA binding for AMD 7950X3D
# Node 0 = CCD0 (cores 0-15) with 96MB 3D V-Cache → use for LLM inference
# Node 1 = CCD1 (cores 16-31) → Docker containers run here

set -euo pipefail

MODEL_PATH=/home/kaan/projects/ai-lead-qualifier/models/Qwen3.5-4B-Q4_K_M.gguf
LLAMA_SERVER="${LLAMA_SERVER:-/home/kaan/llama.cpp/build/bin/llama-server}"
PORT="${PORT:-8080}"
THREADS="${THREADS:-14}"
CTX_SIZE="${CTX_SIZE:-8192}"
PARALLEL="${PARALLEL:-2}"

if [[ ! -f "$MODEL_PATH" ]]; then
    echo "ERROR: Model not found at $MODEL_PATH" >&2
    exit 1
fi

if [[ ! -x "$LLAMA_SERVER" ]]; then
    echo "ERROR: llama-server not found at $LLAMA_SERVER" >&2
    exit 1
fi

echo "Starting llama.cpp server"
echo "  Model:   $MODEL_PATH"
echo "  Threads: $THREADS (NUMA node 0, cores 0-15)"
echo "  Port:    $PORT"
echo "  Ctx:     $CTX_SIZE"

exec numactl --cpunodebind=0 --membind=0 \
  taskset -c 0-15 \
  "$LLAMA_SERVER" \
    --model "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --threads "$THREADS" \
    --parallel "$PARALLEL" \
    --ctx-size "$CTX_SIZE" \
    --mlock \
    --no-mmap
