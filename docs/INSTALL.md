# Install

Two paths exist:

- **Scripted (recommended):** `./install.sh` — an interactive wizard that
  automates everything in this document except the two steps that genuinely
  need a browser (creating the Firefly and Vikunja API tokens).
- **Manual:** the steps below. Each section notes what the installer would
  have done for you, so the two stay in sync.

There is intentionally no `curl | bash` one-liner. This stack holds your
financial data; clone the repository and read what you run.

## Scripted Install

```bash
git clone <repo-url> compass
cd compass
./install.sh
```

The script requires bash ≥ 4 and an interactive terminal. What it does, in
order:

1. **Preflight** — detects the OS (Debian/Ubuntu targeted, others warned),
   verifies `git`/`curl`/`openssl`, offers (never forces) to install Docker
   via get.docker.com, checks the daemon and Compose plugin, and warns below
   ~15 GB free disk or ~6 GB RAM.
2. **Wizard** — prompts for network binding/ports, Telegram token + allowed
   user IDs (validated against the Telegram API when online), Firefly
   `APP_KEY` and DB passwords (random by default), Vikunja DB password,
   Ollama model, GPU passthrough (only when a GPU is detected), your real
   bank/card aliases (written to gitignored `bot/accounts_local.py`),
   optional Open WebUI, and optional encrypted backups (GPG passphrase file
   + daily cron; `rclone config` is handed off to you because OneDrive OAuth
   is interactive). Everything lands in `.env` with mode 600; secrets are
   never echoed back.
3. **Provisioning** — starts Postgres/Firefly/Vikunja/Ollama, waits for each
   HTTP endpoint to become ready, offers to pull the Ollama model, then
   walks you through the two manual token steps (browser) and validates each
   pasted token against the live API before saving it. It can create the
   Vikunja admin user via CLI and the `Work`/`Personal` projects via API.
4. **Verification** — starts the bot, confirms the container stays up, and
   sends you a Telegram test message (or tells you to `/start` the bot first
   if Telegram refuses, which it does until you initiate contact).
5. **Summary** — running services, URLs, and the exact paths of `.env`,
   `bot/accounts_local.py`, and `.backup_passphrase` with a back-these-up
   reminder.

Re-running `./install.sh` on a configured machine offers: keep the config
and just restart/resume, patch individual sections, or reconfigure from
scratch (the old `.env` is backed up first). It never silently overwrites.

`./install.sh --uninstall` removes containers, optionally the backup cron,
and — only behind a typed `DELETE` confirmation — the `data/` directory and
config files.

## Manual Setup

### Prerequisites

- Docker Engine with the Compose plugin
- A Telegram bot token from @BotFather
- Enough disk and memory for Ollama and your chosen model

### 1. Configure Environment

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill (the installer generates the passwords/keys randomly):

- `TELEGRAM_TOKEN` — from @BotFather
- `TELEGRAM_ALLOWED_USER_IDS` — your numeric ID, via @userinfobot
- `CURRENCY` — ISO 4217 code for all amounts (default `INR`)
- `FIREFLY_APP_KEY` — `echo "base64:$(openssl rand -base64 32)"`
- `FIREFLY_DB_PASSWORD`, `VIKUNJA_DB_PASSWORD` — strong random strings
- `PIPELINES_API_KEY` — any random string (only used with Open WebUI)

Set fixed image tags in `.env` before starting services. The template uses
`replace-with-tested-tag` placeholders for images that should not float.

By default, service ports bind to `127.0.0.1`. Change `HOST_BIND` only if you
intend to expose the web UIs beyond the local machine.

### 2. Start Core Services

```bash
mkdir -p data/{ollama,firefly_db,firefly_uploads,vikunja_db,vikunja_files,bot_attachments,openwebui,rag/chroma_db,rag/hf_cache}
# the bot and Vikunja containers run unprivileged as UID 1000
sudo chown 1000 data/bot_attachments data/vikunja_files
docker compose up -d firefly vikunja ollama
```

Open Firefly and Vikunja on the configured host ports and finish their
first-run setup. This part is manual even under the installer:

- **Firefly:** register in the web UI (first user becomes admin), create your
  asset/liability accounts, then Options → Profile → OAuth → Personal Access
  Tokens → create one → put it in `.env` as `FIREFLY_TOKEN`. If `CURRENCY`
  is not `EUR` (Firefly's own default), make it Firefly's default under
  Options → Currencies — the installer does this via the API automatically.
- **Vikunja:** registration is disabled by default; create the admin via CLI:

  ```bash
  docker compose exec vikunja /app/vikunja/vikunja user create \
    -u <username> -e <email> -p <password>
  ```

  Log in, create `Work` and `Personal` projects, then Settings → API Tokens →
  create a token with projects read/create and full task permissions → put it
  in `.env` as `VIKUNJA_TOKEN`.

Pull your Ollama model:

```bash
docker compose exec ollama ollama pull "$(sed -n 's/^OLLAMA_MODEL=//p' .env)"
```

### 3. Customize Accounts And Prompts

Create `bot/accounts_local.py` (gitignored) mapping your real Firefly account
names to short aliases — see [CUSTOMIZATION.md](CUSTOMIZATION.md). The
installer builds this file interactively. Then review the prompt files in
[bot/](../bot/) so categories, tags, and examples match your use case.

### 4. Start The Bot

```bash
docker compose up -d --build compass_bot
```

Send `/start` to your Telegram bot from an allowed user ID (Telegram bots
cannot message you first).

### 5. Optional Extras

Open WebUI (set `COMPOSE_PROFILES=ai-webui` in `.env`, or pass the profile):

```bash
docker compose --profile ai-webui up -d
```

GPU for Ollama: create a `docker-compose.override.yml` (gitignored) adding an
NVIDIA device reservation or a `/dev/dri` mapping to the `ollama` service —
the installer writes this for you when it detects a GPU.

Encrypted backups: see [OPERATIONS.md](OPERATIONS.md); the installer can
generate `.backup_passphrase` and install a daily cron entry (marked
`# compass-backup` in your crontab).
