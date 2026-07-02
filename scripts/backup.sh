#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${COMPASS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
PASSPHRASE_FILE="${PASSPHRASE_FILE:-$ROOT_DIR/.backup_passphrase}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

DATE="$(date +%Y-%m-%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

notify() {
  local msg="$1"
  local tg_token=""
  local tg_user=""

  if [ -f "$ENV_FILE" ]; then
    tg_token="$(grep -E '^TELEGRAM_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)"
    tg_user="$(grep -E '^TELEGRAM_ALLOWED_USER_IDS=' "$ENV_FILE" | cut -d= -f2- | cut -d, -f1 || true)"
  fi

  if [ -n "$tg_token" ] && [ -n "$tg_user" ]; then
    curl -s -X POST "https://api.telegram.org/bot${tg_token}/sendMessage" \
      -d "chat_id=${tg_user}" \
      -d "text=${msg}" >/dev/null || true
  fi
  echo "$msg"
}

trap 'notify "Backup failed on line $LINENO"; exit 1' ERR

if [ ! -f "$PASSPHRASE_FILE" ]; then
  echo "Missing passphrase file: $PASSPHRASE_FILE"
  exit 1
fi

echo "Dumping Firefly Postgres..."
docker compose -f "$ROOT_DIR/docker-compose.yml" exec -T firefly_db \
  pg_dump -U firefly firefly | gzip > "$BACKUP_DIR/firefly_${DATE}.sql.gz"

echo "Dumping Vikunja Postgres..."
docker compose -f "$ROOT_DIR/docker-compose.yml" exec -T vikunja_db \
  pg_dump -U vikunja vikunja | gzip > "$BACKUP_DIR/vikunja_${DATE}.sql.gz"

echo "Archiving repo configuration..."
tar --exclude='.env' \
  --exclude='data' \
  --exclude='backups' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -czf "$BACKUP_DIR/config_${DATE}.tar.gz" \
  -C "$ROOT_DIR" \
  README.md docs scripts bot openwebui docker-compose.yml .env.example .gitignore

echo "Encrypting..."
for f in "$BACKUP_DIR"/*_"$DATE".*; do
  [ -f "$f" ] || continue
  # --passphrase-file keeps the passphrase out of the process list
  gpg --batch --yes --passphrase-file "$PASSPHRASE_FILE" \
    --symmetric --cipher-algo AES256 \
    -o "$f.gpg" "$f"
  rm "$f"
done

if [ -n "$RCLONE_REMOTE" ]; then
  echo "Uploading to $RCLONE_REMOTE..."
  rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" \
    --include "*_${DATE}*.gpg" \
    --transfers 4
  # Only expire our own encrypted backups, never other files on the remote
  rclone delete "$RCLONE_REMOTE" --include "*.gpg" --min-age "${RETENTION_DAYS}d"
fi

find "$BACKUP_DIR" -type f -name "*.gpg" -mtime +"$RETENTION_DAYS" -delete

COUNT="$(find "$BACKUP_DIR" -type f -name "*_${DATE}*.gpg" | wc -l)"
notify "Backup complete: $COUNT encrypted files"
