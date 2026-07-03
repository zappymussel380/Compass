#!/usr/bin/env bash
#
# add-user.sh — provision a new Compass user (owner-driven).
#
# Creates a Firefly III user (with their own isolated financial data), mints
# their API token, sets their default currency, creates their starter
# accounts, creates a Vikunja user with Work/Personal projects and an API
# token, and writes users/<telegram_id>.json for the bot.
#
# Run from the repo root on the machine that hosts the stack. Requires the
# stack to be up and FIREFLY_TOKEN in .env to belong to the Firefly owner
# (the first registered user).

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

ask() {
  local -n __out=$1; local __prompt=$2 __ans
  if [ $# -ge 3 ]; then
    read -r -p "$__prompt [${3:-empty}]: " __ans || die "Input closed."
    __out="${__ans:-$3}"
  else
    while true; do
      read -r -p "$__prompt: " __ans || die "Input closed."
      [ -n "$__ans" ] && { __out="$__ans"; return; }
    done
  fi
}
confirm() {
  local q="$1" def="${2:-N}" hint ans
  [ "$def" = "Y" ] && hint="[Y/n]" || hint="[y/N]"
  while true; do
    read -r -p "$q $hint " ans || die "Input closed."
    ans="${ans:-$def}"
    case "${ans,,}" in y|yes) return 0 ;; n|no) return 1 ;; esac
  done
}
rand_password() { openssl rand -base64 30 | tr -d '=+/' | cut -c1-20; }

[ -t 0 ] || die "add-user.sh is interactive — run it from a terminal."
[ -f "$ENV_FILE" ] || die ".env not found — install Compass first."
set -a; . "$ENV_FILE"; set +a
command -v python3 >/dev/null || die "python3 is required."

HOST="${HOST_BIND:-127.0.0.1}"; [ "$HOST" = "0.0.0.0" ] && HOST="127.0.0.1"
FF="http://$HOST:${FIREFLY_PORT:-24010}"
VK="http://$HOST:${VIKUNJA_PORT:-24030}"
CODE="${CURRENCY:-INR}"

json() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }

ff_owner() { # method path [json-body]
  local m=$1 p=$2 b=${3:-}
  curl -fsS --max-time 20 -X "$m" -H "Authorization: Bearer $FIREFLY_TOKEN" \
    -H "Accept: application/json" -H "Content-Type: application/json" \
    ${b:+-d "$b"} "$FF/api/v1$p"
}

head_line "Compass — add a user"
curl -fsS --max-time 10 -H "Authorization: Bearer $FIREFLY_TOKEN" \
  -H "Accept: application/json" "$FF/api/v1/about" >/dev/null \
  || die "Owner FIREFLY_TOKEN in .env does not validate against $FF."
OWNER_ROLE="$(ff_owner GET /about/user | json "d['data']['attributes'].get('role')")"
[ "$OWNER_ROLE" = "owner" ] || die "FIREFLY_TOKEN belongs to a non-owner user (role: $OWNER_ROLE) — only the owner can create users."
ok "Owner token valid"

ask DISPLAY_NAME "Display name for the new user"
while true; do
  ask TG_ID "Their Telegram user ID (numeric, from @userinfobot)"
  [[ "$TG_ID" =~ ^[0-9]+$ ]] && break
  warn "Numeric IDs only."
done
[ -f "$USERS_DIR/$TG_ID.json" ] && die "users/$TG_ID.json already exists — remove the user first."
while true; do
  ask EMAIL "Their email (Firefly + Vikunja login)"
  [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] && break
  warn "That doesn't look like an email address."
done
DEFAULT_UNAME="$(echo "$DISPLAY_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
ask VK_UNAME "Vikunja username" "${DEFAULT_UNAME:-user$TG_ID}"

say
say "Now their bank/card accounts. These are created in THEIR Firefly space"
say "and their aliases stay private to them."
NAMES=(); ALIASES=(); TYPES=()
while true; do
  ask ACC_NAME "Account name (e.g. 'Main Checking')"
  ask ACC_ALIASES "Short aliases, comma-separated (e.g. 'main, checking, bank')"
  if confirm "Is this a credit card?" N; then TYPES+=("liability"); else TYPES+=("asset"); fi
  NAMES+=("${ACC_NAME//[\"\\$'\n']/}"); ALIASES+=("${ACC_ALIASES//[\"\\$'\n']/}")
  confirm "Add another account?" Y || break
done

FF_PASS="$(rand_password)"
VK_PASS="$(rand_password)"

# --- Firefly user -----------------------------------------------------------
head_line "Firefly III"
FF_USER_JSON="$(ff_owner POST /users "{\"email\":\"$EMAIL\",\"blocked\":false}")" \
  || die "Could not create the Firefly user (does $EMAIL already exist?)."
FF_USER_ID="$(json "d['data']['id']" <<<"$FF_USER_JSON")"
ok "Firefly user created (id $FF_USER_ID)"

# Set their password and mint their personal access token inside the
# container (Firefly has no headless API for either).
PHP_OUT="$($COMPOSE -f "$ROOT_DIR/docker-compose.yml" exec -T firefly php -r "
require '/var/www/html/vendor/autoload.php';
\$app = require '/var/www/html/bootstrap/app.php';
\$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
\$u = FireflyIII\User::where('email', '$EMAIL')->first();
\$u->password = Illuminate\Support\Facades\Hash::make('$FF_PASS');
\$u->save();
echo \"\nJWTLINE:\" . \$u->createToken('compass-bot')->accessToken . \"\n\";
" 2>&1)" || true
FF_USER_TOKEN="$(grep -o 'JWTLINE:eyJ[^ ]*' <<<"$PHP_OUT" | cut -d: -f2 | tr -d '[:space:]')"
[ -n "$FF_USER_TOKEN" ] || die "Could not mint the Firefly token: ${PHP_OUT:0:300}"
ok "Password set + API token minted"

ff_user() { # method path [json-body]
  local m=$1 p=$2 b=${3:-}
  curl -fsS --max-time 20 -X "$m" -H "Authorization: Bearer $FF_USER_TOKEN" \
    -H "Accept: application/json" -H "Content-Type: application/json" \
    ${b:+-d "$b"} "$FF/api/v1$p"
}

ff_user POST "/currencies/$CODE/enable" '{}' >/dev/null \
  && { ff_user POST "/currencies/$CODE/primary" '{}' >/dev/null 2>&1 \
       || ff_user POST "/currencies/$CODE/default" '{}' >/dev/null 2>&1; } \
  && ok "Default currency set to $CODE" \
  || warn "Could not set $CODE as their default currency — set it in the Firefly UI."

for i in "${!NAMES[@]}"; do
  if [ "${TYPES[$i]}" = "asset" ]; then
    BODY="{\"name\":\"${NAMES[$i]}\",\"type\":\"asset\",\"account_role\":\"defaultAsset\",\"currency_code\":\"$CODE\"}"
  else
    BODY="{\"name\":\"${NAMES[$i]}\",\"type\":\"liability\",\"liability_type\":\"debt\",\"liability_direction\":\"credit\",\"interest\":\"0\",\"interest_period\":\"monthly\",\"currency_code\":\"$CODE\"}"
  fi
  ff_user POST /accounts "$BODY" >/dev/null \
    && ok "Created account '${NAMES[$i]}' (${TYPES[$i]})" \
    || warn "Could not create '${NAMES[$i]}' — create it in their Firefly UI."
done

# --- Vikunja user ------------------------------------------------------------
head_line "Vikunja"
$COMPOSE -f "$ROOT_DIR/docker-compose.yml" exec -T vikunja /app/vikunja/vikunja \
  user create -u "$VK_UNAME" -e "$EMAIL" -p "$VK_PASS" >/dev/null \
  || die "Vikunja CLI user creation failed (username or email may already exist)."
ok "Vikunja user '$VK_UNAME' created"

VK_JWT="$(curl -fsS --max-time 15 -H "Content-Type: application/json" \
  -d "{\"username\":\"$VK_UNAME\",\"password\":\"$VK_PASS\"}" "$VK/api/v1/login" \
  | json "d['token']")"
VK_USER_ID="$(curl -fsS --max-time 15 -H "Authorization: Bearer $VK_JWT" "$VK/api/v1/user" | json "d['id']")"
VK_TOKEN="$(curl -fsS --max-time 15 -X PUT -H "Authorization: Bearer $VK_JWT" \
  -H "Content-Type: application/json" \
  -d '{"title":"compass-bot","permissions":{"tasks":["create","read_one","read_all","update","delete"],"projects":["create","read_one","read_all","update"]},"expires_at":"2036-01-01T00:00:00Z"}' \
  "$VK/api/v1/tokens" | json "d['token']")"
[ -n "$VK_TOKEN" ] || die "Could not mint the Vikunja API token."
ok "API token minted (user id $VK_USER_ID)"

for p in Work Personal; do
  curl -fsS --max-time 15 -X PUT -H "Authorization: Bearer $VK_TOKEN" \
    -H "Content-Type: application/json" -d "{\"title\":\"$p\"}" \
    "$VK/api/v1/projects" >/dev/null \
    && ok "Created project '$p'" || warn "Could not create project '$p'."
done

# --- Bot config --------------------------------------------------------------
head_line "Bot configuration"
mkdir -p "$USERS_DIR"
UF="$USERS_DIR/$TG_ID.json"
TG_ID="$TG_ID" DISPLAY_NAME="$DISPLAY_NAME" EMAIL="$EMAIL" \
FF_USER_ID="$FF_USER_ID" FF_USER_TOKEN="$FF_USER_TOKEN" \
VK_USER_ID="$VK_USER_ID" VK_UNAME="$VK_UNAME" VK_TOKEN="$VK_TOKEN" \
python3 - "$UF" <<'PYEOF'
import json, os, sys
data = {
    "telegram_id": int(os.environ["TG_ID"]),
    "name": os.environ["DISPLAY_NAME"],
    "email": os.environ["EMAIL"],
    "firefly_user_id": os.environ["FF_USER_ID"],
    "firefly_token": os.environ["FF_USER_TOKEN"],
    "vikunja_user_id": int(os.environ["VK_USER_ID"]),
    "vikunja_username": os.environ["VK_UNAME"],
    "vikunja_token": os.environ["VK_TOKEN"],
    "accounts": {},
}
with open(sys.argv[1], "w") as f:
    json.dump(data, f, indent=2)
os.chmod(sys.argv[1], 0o600)
PYEOF

# fill the accounts map (name -> aliases); one call per account keeps quoting sane
for i in "${!NAMES[@]}"; do
  ALIAS_CSV="${ALIASES[$i]}" NAME="${NAMES[$i]}" python3 - "$UF" <<'PYEOF'
import json, os, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
aliases = [a.strip().lower() for a in os.environ["ALIAS_CSV"].split(",") if a.strip()]
data["accounts"][os.environ["NAME"]] = aliases
with open(sys.argv[1], "w") as f:
    json.dump(data, f, indent=2)
PYEOF
done
chown 1000:1000 "$UF" 2>/dev/null || warn "Could not chown users/$TG_ID.json to UID 1000 — the bot container must be able to read it."
ok "Wrote users/$TG_ID.json (0600)"

head_line "Done — next steps"
say "1. Restart the bot to load the new user:"
say "     docker compose up -d --force-recreate compass_bot"
say "2. Hand these credentials to $DISPLAY_NAME (they should change both):"
say "     Firefly  $FF  — email: $EMAIL  password: $FF_PASS"
say "     Vikunja  $VK  — username: $VK_UNAME  password: $VK_PASS"
say "3. They message the bot on Telegram with /start."
say
say "Their financial data is isolated by Firefly/Vikunja themselves: the bot"
say "talks to each server with per-user tokens, so users can never see each"
say "other's accounts, transactions, or tasks."
