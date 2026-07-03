#!/usr/bin/env bash
#
# Compass installer — interactive setup for the full stack.
#
# Usage:
#   git clone <repo> && cd compass && ./install.sh
#   ./install.sh --uninstall     tear the stack down (with confirmations)
#   ./install.sh --help
#
# Requires bash >= 4 and an interactive terminal. The script deliberately
# refuses piped stdin (curl | bash): for software that touches your
# financial data you should be able to read what you run.
#
# Secrets are written only to .env, bot/accounts_local.py, and
# .backup_passphrase — all gitignored. They are never echoed in full.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"
OVERRIDE_FILE="$ROOT_DIR/docker-compose.override.yml"
ACCOUNTS_FILE="$ROOT_DIR/bot/accounts_local.py"
PASSPHRASE_FILE="$ROOT_DIR/.backup_passphrase"
CRON_MARKER="# compass-backup"
COMPOSE="docker compose"

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

if [ -t 1 ]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
  C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_OFF=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s! %s%s\n' "$C_YELLOW" "$*" "$C_OFF"; }
err()  { printf '%s✗ %s%s\n' "$C_RED" "$*" "$C_OFF" >&2; }
die()  { err "$*"; exit 1; }
head_line() { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_OFF"; }

trap 'echo; die "Aborted."' INT

# confirm "Question?" [Y|N]  — returns 0 for yes
confirm() {
  local q="$1" def="${2:-N}" hint ans
  if [ "$def" = "Y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  while true; do
    read -r -p "$q $hint " ans || die "Input closed."
    ans="${ans:-$def}"
    case "${ans,,}" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
    esac
  done
}

# ask VAR "Prompt" ["default"]  — empty input takes the default (which may
# itself be empty when explicitly passed); without a default, input is required.
ask() {
  local -n __out=$1
  local __prompt=$2 __ans
  if [ $# -ge 3 ]; then
    local __def=$3
    read -r -p "$__prompt [${__def:-empty}]: " __ans || die "Input closed."
    __out="${__ans:-$__def}"
  else
    while true; do
      read -r -p "$__prompt: " __ans || die "Input closed."
      [ -n "$__ans" ] && { __out="$__ans"; return; }
    done
  fi
}

# ask_secret VAR "Prompt"  — hidden input, must be non-empty
ask_secret() {
  local -n __out=$1
  local __ans
  while true; do
    read -rs -p "$2: " __ans || die "Input closed."
    echo
    [ -n "$__ans" ] && { __out="$__ans"; return; }
    warn "Value cannot be empty."
  done
}

mask() {
  local v="${1:-}"
  if [ -z "$v" ] || [[ "$v" == pending-* ]] || [[ "$v" == replace-with-* ]]; then
    echo "(not set)"
  else
    echo "${v:0:4}…(hidden)"
  fi
}

rand_password() { openssl rand -base64 30 | tr -d '=+/' | cut -c1-24; }
rand_app_key()  { echo "base64:$(openssl rand -base64 32)"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

preflight() {
  head_line "Preflight checks"

  [ "${BASH_VERSINFO[0]}" -ge 4 ] \
    || die "bash >= 4 required (macOS ships 3.2 — 'brew install bash')."

  if [ ! -t 0 ]; then
    die "This installer is interactive and needs a terminal.
  Clone the repo and run it directly:
    git clone https://github.com/zappymussel380/Compass.git compass && cd compass && ./install.sh"
  fi

  [ -f "$ROOT_DIR/docker-compose.yml" ] \
    || die "docker-compose.yml not found next to install.sh — run from the repo root."

  case "$(uname -s)" in
    Linux) : ;;
    *) warn "Untested OS: $(uname -s). Continuing, but expect rough edges." ;;
  esac
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-} ${ID_LIKE:-}" in
      *debian*|*ubuntu*) ok "OS: ${PRETTY_NAME:-Debian-family}" ;;
      *) warn "OS is ${PRETTY_NAME:-unknown}: Compass targets Debian/Ubuntu. Continuing." ;;
    esac
  fi

  local missing=""
  for cmd in git curl openssl; do
    command -v "$cmd" >/dev/null 2>&1 || missing="$missing $cmd"
  done
  [ -z "$missing" ] || die "Missing required tools:$missing — install them and re-run."
  ok "git, curl, openssl present"
  command -v gpg >/dev/null 2>&1 || warn "gpg not found — needed only for encrypted backups."

  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed."
    say  "  The official convenience script (https://get.docker.com) can install it."
    if confirm "Install Docker system-wide now?" N; then
      local sudo_cmd=""
      [ "$(id -u)" -eq 0 ] || sudo_cmd="sudo"
      curl -fsSL https://get.docker.com | $sudo_cmd sh \
        || die "Docker installation failed."
      ok "Docker installed"
    else
      die "Install Docker Engine + the Compose plugin, then re-run:
  https://docs.docker.com/engine/install/"
    fi
  fi
  docker info >/dev/null 2>&1 \
    || die "Docker is installed but the daemon is unreachable (is it running? are you in the docker group?)."
  ok "Docker daemon reachable"

  $COMPOSE version >/dev/null 2>&1 \
    || die "Docker Compose plugin missing — https://docs.docker.com/compose/install/"
  ok "Docker Compose present"

  local avail_kb mem_kb
  avail_kb=$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')
  if [ "${avail_kb:-0}" -lt $((15 * 1024 * 1024)) ]; then
    warn "Less than 15 GB free disk ($(awk "BEGIN{printf \"%.1f\", $avail_kb/1048576}") GB). Images + an Ollama model need roughly that."
  else
    ok "Disk space: $(awk "BEGIN{printf \"%.0f\", $avail_kb/1048576}") GB free"
  fi
  mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
  if [ "$mem_kb" -gt 0 ] && [ "$mem_kb" -lt $((6 * 1024 * 1024)) ]; then
    warn "Less than 6 GB RAM — the default parsing model may not fit. Consider a smaller Ollama model."
  elif [ "$mem_kb" -gt 0 ]; then
    ok "RAM: $(awk "BEGIN{printf \"%.0f\", $mem_kb/1048576}") GB"
  fi
}

# ---------------------------------------------------------------------------
# Config state: defaults, load, write
# ---------------------------------------------------------------------------

declare -A CFG

set_defaults() {
  local tz="Asia/Kolkata"
  [ -r /etc/timezone ] && tz="$(cat /etc/timezone)"

  CFG[OLLAMA_IMAGE]="ollama/ollama:latest"
  CFG[POSTGRES_IMAGE]="postgres:17-alpine"
  CFG[FIREFLY_IMAGE]="fireflyiii/core:version-6"
  CFG[VIKUNJA_IMAGE]="vikunja/vikunja:latest"
  CFG[OPEN_WEBUI_IMAGE]="ghcr.io/open-webui/open-webui:main"
  CFG[PIPELINES_IMAGE]="ghcr.io/open-webui/pipelines:main"

  CFG[HOST_BIND]="127.0.0.1"
  CFG[OLLAMA_PORT]="11434"
  CFG[FIREFLY_PORT]="24010"
  CFG[VIKUNJA_PORT]="24030"
  CFG[OPEN_WEBUI_PORT]="3000"
  CFG[TZ]="$tz"
  CFG[CURRENCY]="INR"

  CFG[TELEGRAM_TOKEN]=""
  CFG[TELEGRAM_ALLOWED_USER_IDS]=""
  CFG[TELEGRAM_ALLOWED_CHAT_IDS]=""
  CFG[DIGEST_TIME]="11:00"
  CFG[REMINDER_TIMES]=""

  CFG[FIREFLY_APP_KEY]=""
  CFG[FIREFLY_DB_PASSWORD]=""
  CFG[FIREFLY_APP_URL]=""
  CFG[FIREFLY_DEFAULT_LANGUAGE]="en_US"
  CFG[FIREFLY_DEFAULT_LOCALE]="en_IN"
  CFG[FIREFLY_TRUSTED_PROXIES]=""
  CFG[FIREFLY_TOKEN]="pending-run-install-again"

  CFG[VIKUNJA_DB_PASSWORD]=""
  CFG[VIKUNJA_PUBLIC_URL]=""
  CFG[VIKUNJA_ENABLE_REGISTRATION]="false"
  CFG[VIKUNJA_TOKEN]="pending-run-install-again"

  CFG[OLLAMA_MODEL]="gemma4:e4b"
  CFG[OLLAMA_KEEP_ALIVE]="-1"
  CFG[OLLAMA_NUM_CTX]="4096"
  CFG[OLLAMA_WARMUP_TIMEOUT]="30"

  CFG[PIPELINES_API_KEY]=""
  CFG[COMPOSE_PROFILES]=""
}

load_env() {
  [ -f "$ENV_FILE" ] || return 0
  local line key val
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    val="${line#*=}"
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    CFG[$key]="$val"
  done < "$ENV_FILE"
}

write_env() {
  local tmp
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  {
    echo "# Compass configuration — generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# This file contains secrets. It is gitignored: BACK IT UP YOURSELF."
    echo
    echo "# --- Image pins (advisory: pin to tested tags for production) ---"
    echo "OLLAMA_IMAGE=${CFG[OLLAMA_IMAGE]}"
    echo "POSTGRES_IMAGE=${CFG[POSTGRES_IMAGE]}"
    echo "FIREFLY_IMAGE=${CFG[FIREFLY_IMAGE]}"
    echo "VIKUNJA_IMAGE=${CFG[VIKUNJA_IMAGE]}"
    echo "OPEN_WEBUI_IMAGE=${CFG[OPEN_WEBUI_IMAGE]}"
    echo "PIPELINES_IMAGE=${CFG[PIPELINES_IMAGE]}"
    echo
    echo "# --- Host binding and ports ---"
    echo "HOST_BIND=${CFG[HOST_BIND]}"
    echo "OLLAMA_PORT=${CFG[OLLAMA_PORT]}"
    echo "FIREFLY_PORT=${CFG[FIREFLY_PORT]}"
    echo "VIKUNJA_PORT=${CFG[VIKUNJA_PORT]}"
    echo "OPEN_WEBUI_PORT=${CFG[OPEN_WEBUI_PORT]}"
    echo
    echo "# --- Locale ---"
    echo "TZ=${CFG[TZ]}"
    echo "CURRENCY=${CFG[CURRENCY]}"
    echo
    echo "# --- Telegram ---"
    echo "TELEGRAM_TOKEN=${CFG[TELEGRAM_TOKEN]}"
    echo "TELEGRAM_ALLOWED_USER_IDS=${CFG[TELEGRAM_ALLOWED_USER_IDS]}"
    echo "TELEGRAM_ALLOWED_CHAT_IDS=${CFG[TELEGRAM_ALLOWED_CHAT_IDS]}"
    echo "DIGEST_TIME=${CFG[DIGEST_TIME]}"
    echo "REMINDER_TIMES=${CFG[REMINDER_TIMES]}"
    echo
    echo "# --- Firefly III ---"
    echo "FIREFLY_APP_KEY=${CFG[FIREFLY_APP_KEY]}"
    echo "FIREFLY_DB_PASSWORD=${CFG[FIREFLY_DB_PASSWORD]}"
    echo "FIREFLY_APP_URL=${CFG[FIREFLY_APP_URL]}"
    echo "FIREFLY_DEFAULT_LANGUAGE=${CFG[FIREFLY_DEFAULT_LANGUAGE]}"
    echo "FIREFLY_DEFAULT_LOCALE=${CFG[FIREFLY_DEFAULT_LOCALE]}"
    echo "FIREFLY_TRUSTED_PROXIES=${CFG[FIREFLY_TRUSTED_PROXIES]}"
    echo "FIREFLY_TOKEN=${CFG[FIREFLY_TOKEN]}"
    echo
    echo "# --- Vikunja ---"
    echo "VIKUNJA_DB_PASSWORD=${CFG[VIKUNJA_DB_PASSWORD]}"
    echo "VIKUNJA_PUBLIC_URL=${CFG[VIKUNJA_PUBLIC_URL]}"
    echo "VIKUNJA_ENABLE_REGISTRATION=${CFG[VIKUNJA_ENABLE_REGISTRATION]}"
    echo "VIKUNJA_TOKEN=${CFG[VIKUNJA_TOKEN]}"
    echo
    echo "# --- Ollama ---"
    echo "OLLAMA_MODEL=${CFG[OLLAMA_MODEL]}"
    echo "OLLAMA_KEEP_ALIVE=${CFG[OLLAMA_KEEP_ALIVE]}"
    echo "OLLAMA_NUM_CTX=${CFG[OLLAMA_NUM_CTX]}"
    echo "OLLAMA_WARMUP_TIMEOUT=${CFG[OLLAMA_WARMUP_TIMEOUT]}"
    echo
    echo "# --- Open WebUI (optional profile) ---"
    echo "PIPELINES_API_KEY=${CFG[PIPELINES_API_KEY]}"
    echo "COMPOSE_PROFILES=${CFG[COMPOSE_PROFILES]}"
  } > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

probe_host() {
  local h="${CFG[HOST_BIND]}"
  [ "$h" = "0.0.0.0" ] && h="127.0.0.1"
  echo "$h"
}

# ---------------------------------------------------------------------------
# Wizard sections
# ---------------------------------------------------------------------------

sec_network() {
  head_line "Network"
  say "Web UIs bind to HOST_BIND. Keep 127.0.0.1 unless this host is protected"
  say "by a firewall/VPN/reverse proxy."
  ask CFG[HOST_BIND] "Bind address" "${CFG[HOST_BIND]}"
  if [ "${CFG[HOST_BIND]}" != "127.0.0.1" ]; then
    warn "Binding to ${CFG[HOST_BIND]} exposes Firefly (your finances) beyond this machine."
    confirm "Are you sure?" N || CFG[HOST_BIND]="127.0.0.1"
  fi
  ask CFG[FIREFLY_PORT]    "Firefly III port"  "${CFG[FIREFLY_PORT]}"
  ask CFG[VIKUNJA_PORT]    "Vikunja port"      "${CFG[VIKUNJA_PORT]}"
  ask CFG[OLLAMA_PORT]     "Ollama port"       "${CFG[OLLAMA_PORT]}"
  ask CFG[TZ]              "Timezone (IANA)"   "${CFG[TZ]}"

  say
  say "Currency for all amounts. The bot shows its symbol and Firefly records"
  say "transactions in it. Common codes: INR USD EUR GBP AUD CAD SGD AED JPY"
  local cur
  while true; do
    ask cur "Currency (ISO 4217 code)" "${CFG[CURRENCY]}"
    cur="$(printf '%s' "$cur" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
    [[ "$cur" =~ ^[A-Z]{3}$ ]] && break
    warn "Enter a 3-letter code, e.g. INR, USD, EUR."
  done
  CFG[CURRENCY]="$cur"
  # en_IN digit grouping (1,00,000) only makes sense for INR deployments.
  if [ "$cur" != "INR" ] && [ "${CFG[FIREFLY_DEFAULT_LOCALE]}" = "en_IN" ]; then
    CFG[FIREFLY_DEFAULT_LOCALE]="en_US"
  fi

  CFG[FIREFLY_APP_URL]="http://$(probe_host):${CFG[FIREFLY_PORT]}"
  CFG[VIKUNJA_PUBLIC_URL]="http://$(probe_host):${CFG[VIKUNJA_PORT]}"
}

sec_telegram() {
  head_line "Telegram"
  say "1. Open Telegram, talk to @BotFather, send /newbot, follow the prompts."
  say "2. BotFather replies with a token like 123456789:AA...xyz"
  local tok
  if [ -n "${CFG[TELEGRAM_TOKEN]}" ]; then
    say "Current token: $(mask "${CFG[TELEGRAM_TOKEN]}")"
    confirm "Keep the existing bot token?" Y && tok="${CFG[TELEGRAM_TOKEN]}"
  fi
  while [ -z "${tok:-}" ]; do
    ask_secret tok "Bot token (input hidden)"
    if ! [[ "$tok" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$ ]]; then
      warn "That doesn't look like a BotFather token."
      confirm "Use it anyway?" N || tok=""
    fi
  done
  CFG[TELEGRAM_TOKEN]="$tok"
  ok "Bot token set"

  if curl -fsS --max-time 10 "https://api.telegram.org/bot${CFG[TELEGRAM_TOKEN]}/getMe" 2>/dev/null | grep -q '"ok":true'; then
    ok "Token verified with the Telegram API"
  else
    warn "Could not verify the token with Telegram (offline, or token invalid). Continuing."
  fi

  say
  say "Find your numeric Telegram user ID by messaging @userinfobot."
  local ids
  while true; do
    ask ids "Allowed Telegram user ID(s), comma-separated" "${CFG[TELEGRAM_ALLOWED_USER_IDS]:-}"
    [[ "$ids" =~ ^[0-9]+(,[0-9]+)*$ ]] && break
    warn "Use numeric IDs only, e.g. 123456789 or 123456789,987654321"
  done
  CFG[TELEGRAM_ALLOWED_USER_IDS]="$ids"
  ok "Allowed users set"

  ask CFG[DIGEST_TIME] "Daily task digest time (HH:MM, empty disables)" "${CFG[DIGEST_TIME]}"
  ask CFG[REMINDER_TIMES] "Reminder nudge times (comma-separated HH:MM, empty disables)" "${CFG[REMINDER_TIMES]}"
}

sec_firefly() {
  head_line "Firefly III"
  if [ -z "${CFG[FIREFLY_APP_KEY]}" ] || [[ "${CFG[FIREFLY_APP_KEY]}" == *replace-with* ]]; then
    if confirm "Generate a random Firefly APP_KEY?" Y; then
      CFG[FIREFLY_APP_KEY]="$(rand_app_key)"
      ok "APP_KEY generated (hidden)"
    else
      ask_secret CFG[FIREFLY_APP_KEY] "Firefly APP_KEY (base64:..., input hidden)"
    fi
  else
    ok "Keeping existing APP_KEY $(mask "${CFG[FIREFLY_APP_KEY]}")"
  fi

  if [ -z "${CFG[FIREFLY_DB_PASSWORD]}" ] || [[ "${CFG[FIREFLY_DB_PASSWORD]}" == *replace-with* ]]; then
    if confirm "Generate a random Firefly DB password?" Y; then
      CFG[FIREFLY_DB_PASSWORD]="$(rand_password)"
      ok "DB password generated (hidden)"
    else
      ask_secret CFG[FIREFLY_DB_PASSWORD] "Firefly DB password (input hidden)"
    fi
  else
    ok "Keeping existing Firefly DB password"
  fi
}

sec_vikunja() {
  head_line "Vikunja"
  if [ -z "${CFG[VIKUNJA_DB_PASSWORD]}" ] || [[ "${CFG[VIKUNJA_DB_PASSWORD]}" == *replace-with* ]]; then
    if confirm "Generate a random Vikunja DB password?" Y; then
      CFG[VIKUNJA_DB_PASSWORD]="$(rand_password)"
      ok "DB password generated (hidden)"
    else
      ask_secret CFG[VIKUNJA_DB_PASSWORD] "Vikunja DB password (input hidden)"
    fi
  else
    ok "Keeping existing Vikunja DB password"
  fi
}

sec_ollama() {
  head_line "Ollama (local parsing model)"
  ask CFG[OLLAMA_MODEL] "Model to use" "${CFG[OLLAMA_MODEL]}"

  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    say "NVIDIA GPU detected."
    if confirm "Give the Ollama container GPU access? (needs nvidia-container-toolkit)" Y; then
      write_gpu_override nvidia
    fi
  elif [ -d /dev/dri ]; then
    say "A /dev/dri render device exists (integrated/AMD GPU)."
    say "Note: the default Ollama image only accelerates NVIDIA; AMD needs the"
    say "ollama/ollama:rocm image and a supported card. Most iGPUs won't help."
    if confirm "Map /dev/dri into the Ollama container anyway?" N; then
      write_gpu_override dri
    fi
  else
    say "No GPU detected — Ollama will run on CPU."
  fi
}

write_gpu_override() {
  if [ -f "$OVERRIDE_FILE" ]; then
    confirm "docker-compose.override.yml exists. Replace it?" N || return 0
  fi
  case "$1" in
    nvidia)
      cat > "$OVERRIDE_FILE" <<'YAML'
# Generated by install.sh — gives the Ollama container NVIDIA GPU access.
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
YAML
      ;;
    dri)
      cat > "$OVERRIDE_FILE" <<'YAML'
# Generated by install.sh — maps the host render device into Ollama.
services:
  ollama:
    devices:
      - /dev/dri:/dev/dri
YAML
      ;;
  esac
  ok "Wrote $OVERRIDE_FILE"
}

sec_accounts() {
  head_line "Bank & card accounts"
  say "Compass resolves short aliases (\"checking\", \"rewards\") to your real"
  say "Firefly account names. Your real names go into bot/accounts_local.py,"
  say "which is gitignored and never leaves this machine."
  say "Use the EXACT account names you will create in Firefly III."

  if [ -f "$ACCOUNTS_FILE" ]; then
    say "bot/accounts_local.py already exists."
    confirm "Keep it as-is?" Y && return 0
  fi
  confirm "Set up your account aliases now?" Y || {
    say "Skipped — edit bot/accounts_local.py later (see docs/CUSTOMIZATION.md)."
    return 0
  }

  local names=() aliases=() name alias_csv
  while true; do
    ask name "Account name exactly as it will appear in Firefly (e.g. 'Main Checking')"
    ask alias_csv "Short aliases for it, comma-separated (e.g. 'main, checking, bank')"
    # keep the generated python trivially safe
    name="${name//[\"\\$'\n']/}"
    alias_csv="${alias_csv//[\"\\$'\n']/}"
    names+=("$name"); aliases+=("$alias_csv")
    confirm "Add another account?" Y || break
  done

  {
    echo '"""Private account aliases for this deployment. Generated by install.sh.'
    echo
    echo 'This file is gitignored. Keep your real Firefly account names here instead'
    echo 'of publishing them in bot/accounts.py.'
    echo '"""'
    echo
    echo 'ACCOUNTS = {'
    local i a parts out
    for i in "${!names[@]}"; do
      out=""
      IFS=',' read -ra parts <<< "${aliases[$i]}"
      for a in "${parts[@]}"; do
        a="$(echo "$a" | sed 's/^ *//; s/ *$//' | tr '[:upper:]' '[:lower:]')"
        [ -n "$a" ] && out="$out\"$a\", "
      done
      printf '    "%s": [%s],\n' "${names[$i]}" "${out%, }"
    done
    echo '}'
  } > "$ACCOUNTS_FILE"
  ok "Wrote bot/accounts_local.py (${#names[@]} account(s), gitignored)"
}

sec_backups() {
  head_line "Encrypted backups (optional)"
  say "scripts/backup.sh dumps both databases, encrypts them with GPG, and can"
  say "push them to any rclone remote (OneDrive, Drive, S3...)."
  confirm "Set up encrypted backups now?" N || {
    say "Skipped — see docs/OPERATIONS.md when you want them."
    return 0
  }

  command -v gpg >/dev/null 2>&1 || { warn "gpg is not installed — install it and re-run this section."; return 0; }

  if [ -f "$PASSPHRASE_FILE" ]; then
    ok "Keeping existing backup passphrase file"
  else
    (umask 077 && openssl rand -base64 32 > "$PASSPHRASE_FILE")
    ok "Generated $PASSPHRASE_FILE (chmod 600)"
    warn "Store a copy of this passphrase somewhere safe — without it your backups are unreadable."
  fi

  local remote=""
  if ! command -v rclone >/dev/null 2>&1; then
    warn "rclone is not installed. To enable offsite copies later:"
    say  "  1. Install rclone (https://rclone.org/install/)"
    say  "  2. Run 'rclone config' (OneDrive needs interactive OAuth — the"
    say  "     installer cannot automate that step)"
    say  "  3. Re-run ./install.sh and redo this section."
  else
    if rclone listremotes 2>/dev/null | grep -q .; then
      say "Configured rclone remotes:"
      rclone listremotes | sed 's/^/    /'
    else
      say "No rclone remotes configured yet."
      if confirm "Run 'rclone config' now? (interactive; OneDrive uses browser OAuth)" N; then
        rclone config || warn "rclone config did not finish cleanly."
      fi
    fi
    ask remote "rclone remote path for backups (e.g. 'onedrive:compass-backups', empty = local only)" ""
  fi

  if confirm "Install a daily 02:00 backup cron job?" Y; then
    command -v crontab >/dev/null 2>&1 || { warn "crontab not available — schedule scripts/backup.sh yourself."; return 0; }
    local line="0 2 * * * RCLONE_REMOTE=$remote $ROOT_DIR/scripts/backup.sh >> $ROOT_DIR/backups/backup.log 2>&1 $CRON_MARKER"
    ( crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" || true; echo "$line" ) | crontab -
    mkdir -p "$ROOT_DIR/backups"
    ok "Cron installed (marker: $CRON_MARKER)"
  fi
}

sec_webui() {
  head_line "Open WebUI (optional)"
  say "A local chat UI on top of Ollama. Not needed for the Telegram bot."
  if confirm "Enable Open WebUI + Pipelines?" N; then
    CFG[COMPOSE_PROFILES]="ai-webui"
    [ -n "${CFG[PIPELINES_API_KEY]}" ] && [[ "${CFG[PIPELINES_API_KEY]}" != *replace-with* ]] \
      || CFG[PIPELINES_API_KEY]="$(rand_password)"
    ok "Open WebUI enabled on port ${CFG[OPEN_WEBUI_PORT]}"
  else
    CFG[COMPOSE_PROFILES]=""
    [ -n "${CFG[PIPELINES_API_KEY]}" ] || CFG[PIPELINES_API_KEY]="$(rand_password)"
  fi
}

run_full_wizard() {
  sec_network
  sec_telegram
  sec_firefly
  sec_vikunja
  sec_ollama
  sec_accounts
  sec_webui
  write_env
  ok ".env written (chmod 600)"
  sec_backups
}

patch_wizard() {
  while true; do
    head_line "Reconfigure — pick a section"
    say "  1) Network / locale / currency   5) Ollama model & GPU"
    say "  2) Telegram                      6) Bank account aliases"
    say "  3) Firefly III secrets           7) Backups"
    say "  4) Vikunja secrets               8) Open WebUI"
    say "  d) Done — save and continue"
    local choice
    read -r -p "Section: " choice || die "Input closed."
    case "$choice" in
      1) sec_network ;;
      2) sec_telegram ;;
      3) sec_firefly ;;
      4) sec_vikunja ;;
      5) sec_ollama ;;
      6) sec_accounts ;;
      7) sec_backups ;;
      8) sec_webui ;;
      d|D) break ;;
      *) warn "Unknown choice." ;;
    esac
  done
  write_env
  ok ".env updated"
}

# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

make_dirs() {
  mkdir -p \
    "$ROOT_DIR/data/ollama" \
    "$ROOT_DIR/data/firefly_db" \
    "$ROOT_DIR/data/firefly_uploads" \
    "$ROOT_DIR/data/vikunja_db" \
    "$ROOT_DIR/data/vikunja_files" \
    "$ROOT_DIR/data/bot_attachments" \
    "$ROOT_DIR/data/openwebui" \
    "$ROOT_DIR/data/rag/chroma_db" \
    "$ROOT_DIR/data/rag/hf_cache"
  # the bot and Vikunja containers run unprivileged as UID 1000
  chown 1000 "$ROOT_DIR/data/bot_attachments" 2>/dev/null \
    || warn "Could not chown data/bot_attachments to UID 1000 — receipts may fail to save."
  chown 1000 "$ROOT_DIR/data/vikunja_files" 2>/dev/null \
    || warn "Could not chown data/vikunja_files to UID 1000 — Vikunja will fail to start."
}

wait_http() { # wait_http <url> <name> <timeout-seconds> <compose-service>
  local url=$1 name=$2 timeout=$3 service=${4:-} waited=0
  printf '  waiting for %s ' "$name"
  while ! curl -fsS --max-time 5 -o /dev/null "$url" 2>/dev/null; do
    waited=$((waited + 5))
    if [ "$waited" -ge "$timeout" ]; then
      echo
      err "$name did not become ready within ${timeout}s."
      say "  Check: $COMPOSE logs $service"
      return 1
    fi
    printf '.'
    sleep 5
  done
  echo
  ok "$name is up"
}

start_core() {
  head_line "Starting services"
  say "Pulling images and starting Postgres, Firefly, Vikunja, Ollama."
  say "First run downloads several GB — this can take a while."
  (cd "$ROOT_DIR" && $COMPOSE up -d firefly vikunja ollama)

  wait_http "http://$(probe_host):${CFG[FIREFLY_PORT]}/"            "Firefly III" 300 firefly
  wait_http "http://$(probe_host):${CFG[VIKUNJA_PORT]}/api/v1/info" "Vikunja"     180 vikunja
  wait_http "http://$(probe_host):${CFG[OLLAMA_PORT]}/api/version"  "Ollama"      180 ollama
}

pull_model() {
  head_line "Ollama model"
  if (cd "$ROOT_DIR" && $COMPOSE exec -T ollama ollama list 2>/dev/null) | awk '{print $1}' | grep -qx "${CFG[OLLAMA_MODEL]}"; then
    ok "Model ${CFG[OLLAMA_MODEL]} already present"
    return 0
  fi
  say "Model: ${CFG[OLLAMA_MODEL]} (multi-GB download)"
  if confirm "Pull it now?" Y; then
    (cd "$ROOT_DIR" && $COMPOSE exec ollama ollama pull "${CFG[OLLAMA_MODEL]}") \
      || { warn "Model pull failed — pull it later with: $COMPOSE exec ollama ollama pull ${CFG[OLLAMA_MODEL]}"; return 0; }
    ok "Model ready"
  else
    warn "Skipped. The bot cannot parse messages until the model is pulled."
  fi
}

token_is_pending() {
  local v="${1:-}"
  [ -z "$v" ] || [[ "$v" == pending-* ]] || [[ "$v" == replace-with-* ]]
}

firefly_token_step() {
  head_line "Firefly III first-run + API token"
  local base="http://$(probe_host):${CFG[FIREFLY_PORT]}"

  if ! token_is_pending "${CFG[FIREFLY_TOKEN]}"; then
    if curl -fsS --max-time 10 -H "Authorization: Bearer ${CFG[FIREFLY_TOKEN]}" \
        -H "Accept: application/json" "$base/api/v1/about" >/dev/null 2>&1; then
      ok "Existing Firefly token is valid"
      return 0
    fi
    warn "Stored Firefly token no longer validates — let's replace it."
  fi

  say "Firefly III cannot create its admin account or API tokens headlessly,"
  say "so this part is manual (once):"
  say "  1. Open  ${CFG[FIREFLY_APP_URL]}  in a browser"
  say "  2. Register — the first account becomes the admin"
  say "  3. Create the asset/liability accounts you named in the wizard"
  say "  4. Options → Profile → OAuth → Personal Access Tokens → Create new token"
  say "  5. Copy the (very long) token"
  say
  while true; do
    confirm "Paste the Firefly token now? ('n' skips — re-run ./install.sh later)" Y || {
      warn "Skipping. The bot will NOT start until this token is set."
      return 1
    }
    local tok
    ask_secret tok "Firefly personal access token (input hidden)"
    if curl -fsS --max-time 10 -H "Authorization: Bearer $tok" \
        -H "Accept: application/json" "$base/api/v1/about" >/dev/null 2>&1; then
      CFG[FIREFLY_TOKEN]="$tok"
      write_env
      ok "Firefly token validated and saved"
      return 0
    fi
    warn "Token was rejected by $base/api/v1/about — try again."
  done
}

firefly_currency_step() {
  # Uses Firefly's native multi-currency support: enable the chosen currency
  # and make it the user's primary/default. Both calls are idempotent. The
  # empty JSON body matters: Firefly 6.6 answers 415 without a JSON
  # Content-Type. 'primary' is the 6.6+ route; older 6.x called it 'default'.
  local code="${CFG[CURRENCY]:-INR}"
  local base="http://$(probe_host):${CFG[FIREFLY_PORT]}"
  ff_currency_post() {
    curl -fsS --max-time 10 -X POST \
      -H "Authorization: Bearer ${CFG[FIREFLY_TOKEN]}" -H "Accept: application/json" \
      -H "Content-Type: application/json" -d '{}' \
      "$base/api/v1/currencies/$code/$1" >/dev/null 2>&1
  }
  if ff_currency_post enable && { ff_currency_post primary || ff_currency_post default; }; then
    ok "Firefly default currency set to $code"
  else
    warn "Could not set Firefly's default currency to $code via the API."
    say  "  Set it manually: Firefly → Options → Currencies → make $code default."
  fi
}

vikunja_admin_step() {
  say "Vikunja registration is disabled for safety; create the admin via CLI."
  confirm "Create a Vikunja user now?" Y || return 0
  local vuser vemail
  ask vuser "Vikunja username"
  ask vemail "Vikunja email" "$vuser@example.invalid"
  # Password is typed into Vikunja's own prompt so it never appears in argv.
  if (cd "$ROOT_DIR" && $COMPOSE exec vikunja /app/vikunja/vikunja user create \
      -u "$vuser" -e "$vemail"); then
    ok "Vikunja user '$vuser' created"
  else
    warn "CLI user creation failed (user may already exist, or the image layout changed)."
    say  "  Fallback: set VIKUNJA_ENABLE_REGISTRATION=true in .env, re-run, register"
    say  "  in the web UI, then set it back to false."
  fi
}

vikunja_token_step() {
  head_line "Vikunja first-run + API token"
  local base="http://$(probe_host):${CFG[VIKUNJA_PORT]}"

  if ! token_is_pending "${CFG[VIKUNJA_TOKEN]}"; then
    if curl -fsS --max-time 10 -H "Authorization: Bearer ${CFG[VIKUNJA_TOKEN]}" \
        "$base/api/v1/projects" >/dev/null 2>&1; then
      ok "Existing Vikunja token is valid"
      return 0
    fi
    warn "Stored Vikunja token no longer validates — let's replace it."
  fi

  vikunja_admin_step

  say "Now create an API token:"
  say "  1. Open  ${CFG[VIKUNJA_PUBLIC_URL]}  and log in"
  say "  2. Settings → API Tokens → create a token with AT LEAST:"
  say "     projects: read all, create  |  tasks: all permissions"
  say
  while true; do
    confirm "Paste the Vikunja token now? ('n' skips — re-run ./install.sh later)" Y || {
      warn "Skipping. The bot will NOT start until this token is set."
      return 1
    }
    local tok
    ask_secret tok "Vikunja API token (input hidden)"
    if curl -fsS --max-time 10 -H "Authorization: Bearer $tok" \
        "$base/api/v1/projects" >/dev/null 2>&1; then
      CFG[VIKUNJA_TOKEN]="$tok"
      write_env
      ok "Vikunja token validated and saved"
      ensure_vikunja_projects "$base" "$tok"
      return 0
    fi
    warn "Token was rejected by $base/api/v1/projects — check its permissions and try again."
  done
}

ensure_vikunja_projects() {
  local base=$1 tok=$2 existing p
  existing="$(curl -fsS --max-time 10 -H "Authorization: Bearer $tok" "$base/api/v1/projects" 2>/dev/null || true)"
  for p in Work Personal; do
    if ! grep -q "\"title\":\"$p\"" <<< "$existing"; then
      if curl -fsS --max-time 10 -X PUT -H "Authorization: Bearer $tok" \
          -H "Content-Type: application/json" \
          -d "{\"title\":\"$p\"}" "$base/api/v1/projects" >/dev/null 2>&1; then
        ok "Created Vikunja project '$p'"
      else
        warn "Could not create Vikunja project '$p' (token may lack projects:create) — create it in the UI."
      fi
    fi
  done
}

start_bot() {
  head_line "Compass bot"
  if token_is_pending "${CFG[FIREFLY_TOKEN]}" || token_is_pending "${CFG[VIKUNJA_TOKEN]}"; then
    warn "Bot not started: Firefly/Vikunja tokens missing."
    say  "  Finish the token steps and re-run ./install.sh — it will resume here."
    return 1
  fi
  (cd "$ROOT_DIR" && $COMPOSE up -d --build compass_bot)
  # bring up any profile services (Open WebUI) too
  (cd "$ROOT_DIR" && $COMPOSE up -d)
  ok "Bot container started"
}

# ---------------------------------------------------------------------------
# Verification & summary
# ---------------------------------------------------------------------------

verify_bot() {
  head_line "Verification"
  sleep 5
  local state
  state="$(docker inspect -f '{{.State.Status}}' compass_bot 2>/dev/null || echo missing)"
  if [ "$state" != "running" ]; then
    err "compass_bot is '$state' — check: $COMPOSE logs compass_bot"
    return 1
  fi
  ok "Bot container running"

  local first_id="${CFG[TELEGRAM_ALLOWED_USER_IDS]%%,*}"
  local resp
  resp="$(curl -fsS --max-time 10 "https://api.telegram.org/bot${CFG[TELEGRAM_TOKEN]}/sendMessage" \
          -d "chat_id=$first_id" \
          --data-urlencode "text=✅ Compass is installed and running. Send /start to begin." 2>/dev/null || true)"
  if grep -q '"ok":true' <<< "$resp"; then
    ok "Test message sent to Telegram user $first_id — check your Telegram"
  else
    local botname
    botname="$(curl -fsS --max-time 10 "https://api.telegram.org/bot${CFG[TELEGRAM_TOKEN]}/getMe" 2>/dev/null \
               | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
    warn "Couldn't message you yet (Telegram only lets bots reply after you start them)."
    say  "  Open https://t.me/${botname:-your-bot} and send /start, then any expense message."
  fi
}

summary() {
  head_line "Summary"
  (cd "$ROOT_DIR" && $COMPOSE ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null) || true
  say
  say "URLs:"
  say "  Firefly III : ${CFG[FIREFLY_APP_URL]}"
  say "  Vikunja     : ${CFG[VIKUNJA_PUBLIC_URL]}"
  [ "${CFG[COMPOSE_PROFILES]}" = "ai-webui" ] \
    && say "  Open WebUI  : http://$(probe_host):${CFG[OPEN_WEBUI_PORT]}"
  say
  say "Files to back up yourself (all gitignored — losing .env means losing the bot):"
  say "  $ENV_FILE"
  [ -f "$ACCOUNTS_FILE" ]   && say "  $ACCOUNTS_FILE"
  [ -f "$PASSPHRASE_FILE" ] && say "  $PASSPHRASE_FILE"
  say
  say "Advisory: images default to rolling tags — pin tested versions in .env"
  say "for production (OLLAMA_IMAGE, FIREFLY_IMAGE, ...)."
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

uninstall() {
  head_line "Uninstall Compass"
  [ -f "$ENV_FILE" ] || warn "No .env found — will still try to remove containers."

  confirm "Stop and remove all Compass containers?" N || die "Uninstall aborted."
  (cd "$ROOT_DIR" && $COMPOSE down --remove-orphans) || warn "compose down reported errors."
  ok "Containers removed"

  if crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
    ( crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" || true ) | crontab -
    ok "Backup cron entry removed"
  fi

  if [ -d "$ROOT_DIR/data" ]; then
    warn "data/ holds your FINANCIAL DATABASES, uploads, and model files."
    if confirm "Delete data/ permanently?" N; then
      local typed
      read -r -p "Type DELETE to confirm irreversible deletion: " typed
      if [ "$typed" = "DELETE" ]; then
        rm -rf "$ROOT_DIR/data"
        ok "data/ deleted"
      else
        say "Not confirmed — data/ kept."
      fi
    else
      say "data/ kept."
    fi
  fi

  local cfgs=()
  [ -f "$ENV_FILE" ] && cfgs+=("$ENV_FILE")
  [ -f "$PASSPHRASE_FILE" ] && cfgs+=("$PASSPHRASE_FILE")
  [ -f "$ACCOUNTS_FILE" ] && cfgs+=("$ACCOUNTS_FILE")
  [ -f "$OVERRIDE_FILE" ] && cfgs+=("$OVERRIDE_FILE")
  if [ "${#cfgs[@]}" -gt 0 ]; then
    say "Config files: ${cfgs[*]}"
    if confirm "Delete these config files (secrets) too?" N; then
      local typed
      read -r -p "Type DELETE to confirm: " typed
      if [ "$typed" = "DELETE" ]; then
        rm -f "${cfgs[@]}"
        ok "Config files deleted"
      else
        say "Not confirmed — config kept."
      fi
    fi
  fi
  say "Done. Docker images were kept; remove them with 'docker image prune -a' if wanted."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

main() {
  case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --uninstall)
      [ -t 0 ] || die "Uninstall is interactive — run it from a terminal."
      uninstall; exit 0 ;;
    "") : ;;
    *) usage; die "Unknown option: $1" ;;
  esac

  say "${C_BOLD}Compass installer${C_OFF}"
  preflight
  set_defaults

  if [ -f "$ENV_FILE" ]; then
    load_env
    head_line "Existing configuration found"
    say "A .env already exists. Never silently overwritten. Options:"
    say "  1) Keep it — just (re)start services and resume any pending steps"
    say "  2) Patch — reconfigure specific sections"
    say "  3) Reconfigure from scratch (existing .env is backed up first)"
    say "  4) Abort"
    local choice
    while true; do
      read -r -p "Choice [1]: " choice || die "Input closed."
      choice="${choice:-1}"
      case "$choice" in
        1) break ;;
        2) patch_wizard; break ;;
        3)
          local bak="$ENV_FILE.bak-$(date +%Y%m%d%H%M%S)"
          cp "$ENV_FILE" "$bak" && chmod 600 "$bak"
          ok "Backed up existing .env to $bak"
          set_defaults
          run_full_wizard
          break ;;
        4) die "Aborted — nothing changed." ;;
        *) warn "Pick 1-4." ;;
      esac
    done
  else
    run_full_wizard
  fi

  make_dirs
  start_core
  pull_model

  # Run both token steps even if one is skipped, so progress isn't lost.
  local tokens_ok=1
  if firefly_token_step; then
    firefly_currency_step
  else
    tokens_ok=0
  fi
  vikunja_token_step || tokens_ok=0

  if [ "$tokens_ok" -eq 1 ] && start_bot; then
    verify_bot || true
  fi
  summary
}

main "$@"
