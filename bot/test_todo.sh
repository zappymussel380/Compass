#!/bin/bash
set -euo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"

TODAY=$(date +%Y-%m-%d)
SYSTEM=$(cat todo_prompt.txt | sed "s/{today}/$TODAY/")
SYSTEM_JSON=$(echo "$SYSTEM" | jq -Rs .)
USER=$(echo "$1" | jq -Rs .)

curl -s "$OLLAMA_URL/api/chat" -d "{
  \"model\": \"$OLLAMA_MODEL\",
  \"stream\": false,
  \"format\": \"json\",
  \"think\": false,
  \"messages\": [
    {\"role\": \"system\", \"content\": $SYSTEM_JSON},
    {\"role\": \"user\", \"content\": $USER}
  ]
}" | jq -r '.message.content' | jq .
