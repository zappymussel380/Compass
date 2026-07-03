#!/usr/bin/env bash
#
# remove-user.sh — remove a Compass user (owner-driven).
#
# Deletes the user's Firefly III account (ALL their financial data), their
# Vikunja account (ALL their tasks), and their users/<telegram_id>.json.
# Every destructive step sits behind an explicit typed confirmation.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
USERS_DIR="$ROOT_DIR/users"
COMPOSE="docker compose"

if [ -t 1 ]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_OFF=""
fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s! %s%s\n' "$C_YELLOW" "$*" "$C_OFF"; }
die()  { printf '%s✗ %s%s\n' "$C_RED" "$*" "$C_OFF" >&2; exit 1; }
head_line() { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_OFF"; }

[ -t 0 ] || die "remove-user.sh is interactive — run it from a terminal."
[ -f "$ENV_FILE" ] || die ".env not found."
set -a; . "$ENV_FILE"; set +a
command -v python3 >/dev/null || die "python3 is required."

HOST="${HOST_BIND:-127.0.0.1}"; [ "$HOST" = "0.0.0.0" ] && HOST="127.0.0.1"
FF="http://$HOST:${FIREFLY_PORT:-24010}"

head_line "Compass — remove a user"
say "Configured users:"
found=0
for f in "$USERS_DIR"/*.json; do
  [ -e "$f" ] || continue
  found=1
  python3 -c "
import json
d = json.load(open('$f'))
print(f\"  {d['telegram_id']:<12} {d.get('name','?')}  ({d.get('email','no email recorded')})\")"
done
[ "$found" -eq 1 ] || die "No users configured under users/."

read -r -p "Telegram ID of the user to remove: " TG_ID || die "Input closed."
[[ "$TG_ID" =~ ^[0-9]+$ ]] || die "Numeric ID expected."
UF="$USERS_DIR/$TG_ID.json"
[ -f "$UF" ] || die "users/$TG_ID.json not found."

NAME="$(python3 -c "import json; print(json.load(open('$UF')).get('name','?'))")"
FF_USER_ID="$(python3 -c "import json; print(json.load(open('$UF')).get('firefly_user_id',''))")"
VK_USER_ID="$(python3 -c "import json; print(json.load(open('$UF')).get('vikunja_user_id',''))")"

warn "This permanently deletes ALL of $NAME's data:"
say  "  - their Firefly user (accounts, transactions, receipts)"
say  "  - their Vikunja user (projects, tasks)"
say  "  - users/$TG_ID.json (their bot access)"
read -r -p "Type DELETE to confirm: " TYPED || die "Input closed."
[ "$TYPED" = "DELETE" ] || die "Not confirmed — nothing was changed."

if [ -n "$FF_USER_ID" ]; then
  OWN_ID="$(curl -fsS --max-time 15 -H "Authorization: Bearer $FIREFLY_TOKEN" \
    -H "Accept: application/json" "$FF/api/v1/about/user" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['id'])")"
  if [ "$OWN_ID" = "$FF_USER_ID" ]; then
    die "That user IS the Firefly owner (the bot's admin token). Refusing to delete the owner."
  fi
  if curl -fsS --max-time 20 -X DELETE -H "Authorization: Bearer $FIREFLY_TOKEN" \
      -H "Accept: application/json" "$FF/api/v1/users/$FF_USER_ID" -o /dev/null; then
    ok "Firefly user $FF_USER_ID deleted"
  else
    warn "Firefly deletion failed — delete the user in Firefly's admin UI."
  fi
else
  warn "No firefly_user_id recorded (migrated legacy user?) — Firefly data left untouched."
fi

if [ -n "$VK_USER_ID" ]; then
  if $COMPOSE -f "$ROOT_DIR/docker-compose.yml" exec -T vikunja \
      /app/vikunja/vikunja user delete "$VK_USER_ID" --now >/dev/null 2>&1; then
    ok "Vikunja user $VK_USER_ID deleted"
  else
    warn "Vikunja deletion failed — remove the user via the Vikunja CLI manually."
  fi
else
  warn "No vikunja_user_id recorded (migrated legacy user?) — Vikunja data left untouched."
fi

rm -f "$UF"
ok "users/$TG_ID.json removed"
say
say "Restart the bot to drop them from the allowlist:"
say "  docker compose up -d --force-recreate compass_bot"
