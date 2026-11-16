#!/usr/bin/env bash
# Optional foreground launcher for the llama.cpp-based Ollama server.
# Environment here must reach the server; putting these in backend/.env is insufficient.
set -euo pipefail
if ! command -v ollama >/dev/null 2>&1; then
  printf '%s\n' 'ollama 명령을 찾지 못했습니다. 설치 경로를 확인하세요.' >&2
  exit 1
fi
if curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  printf '%s\n' 'Ollama가 이미 실행 중입니다. 진행 중인 작업을 마친 뒤 기존 Ollama 앱/서버를 종료하고 다시 실행하세요.' >&2
  exit 1
fi
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_CONTEXT_LENGTH=16384
# llama.cpp prompt-cache budget in MiB. 0 disables retained copies of old prompts.
# This differs from the active context's KV cache. Other Ollama runners may ignore it.
export LLAMA_ARG_CACHE_RAM=0
exec ollama serve
