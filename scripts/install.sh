#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi

mkdir -p \
  data/ollama \
  data/firefly_db \
  data/firefly_uploads \
  data/vikunja_db \
  data/vikunja_files \
  data/bot_attachments \
  data/openwebui \
  data/rag/chroma_db \
  data/rag/hf_cache

placeholder_file="$(mktemp)"
if grep -nE '^(OLLAMA_IMAGE|FIREFLY_IMAGE|VIKUNJA_IMAGE|POSTGRES_IMAGE)=' .env \
  | grep -E 'replace-with|replace-with-tested-tag' >"$placeholder_file" 2>/dev/null; then
  echo "Runtime directories are ready, but required image settings still have placeholders:"
  cat "$placeholder_file"
  rm -f "$placeholder_file"
  echo
  echo "Edit these image values in .env before starting services."
  exit 0
fi
rm -f "$placeholder_file"

cat <<'MSG'
Compass installer scaffold complete.

Next steps:
1. Generate a Firefly app key if you still need one:
   docker run --rm "$(sed -n 's/^FIREFLY_IMAGE=//p' .env)" php artisan key:generate --show
2. Run: docker compose up -d firefly vikunja ollama
3. Create Firefly and Vikunja API tokens, then add them to .env.
4. Pull the Ollama model:
   docker compose exec ollama ollama pull "$(sed -n 's/^OLLAMA_MODEL=//p' .env)"
5. Run: docker compose up -d --build compass_bot

The bot will not start until TELEGRAM_TOKEN, FIREFLY_TOKEN, and VIKUNJA_TOKEN
are set in .env.
MSG
