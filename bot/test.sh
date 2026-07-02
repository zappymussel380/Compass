#!/bin/bash
# Usage: ./test.sh "your expense message here"
set -euo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"

SYSTEM=$(cat system_prompt.txt | jq -Rs .)
USER=$(echo "$1" | jq -Rs .)

curl -s "$OLLAMA_URL/api/chat" -d "{
  \"model\": \"$OLLAMA_MODEL\",
  \"stream\": false,
  \"format\": \"json\",
  \"messages\": [
    {\"role\": \"system\", \"content\": $SYSTEM},
    {\"role\": \"user\", \"content\": $USER}
  ]
}" | jq -r '.message.content' | jq .
