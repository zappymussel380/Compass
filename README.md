# Compass

Compass is a self-hosted Telegram bot for natural-language personal finance and
todo capture. Send a short message like `swiggy 250 lunch checking personal` or
`remind me to file GST by friday`; Compass parses it with a local Ollama model,
asks for confirmation in Telegram, then writes the final record to Firefly III
or Vikunja.

The project is designed for people who want a private, local-first finance and
task capture flow with web UIs for review and reporting.

## What It Does

- Logs expenses, income, transfers, and card payments into Firefly III.
- Creates todos, due dates, priorities, recurring tasks, and quick actions in
  Vikunja.
- Uses a local Ollama model for parsing; no hosted LLM API is required.
- Asks for Telegram confirmation before writing transactions or tasks.
- Supports receipt/bill attachments for Firefly transactions.
- Provides Telegram commands for balances, categories, transaction reports, and
  task digests.
- Includes optional Open WebUI and Pipelines services for local chat/RAG use.

## Architecture

```text
Telegram
   |
   v
Compass bot  ---->  Ollama
   |                  |
   |                  `-- local parsing model
   |
   |----> Firefly III ----> Postgres
   |
   `----> Vikunja --------> Postgres

Optional:
Open WebUI ----> Pipelines ----> Ollama
```

Everything runs through Docker Compose. Runtime data is stored under `data/`,
which is ignored by git.

## Repository Layout

```text
.
|-- docker-compose.yml        # service stack
|-- .env.example              # root configuration template
|-- README.md
|-- SECURITY.md
|-- bot/
|   |-- bot.py                # Telegram bot entrypoint
|   |-- accounts.py           # generic account alias template
|   |-- accounts_local.py     # optional ignored private account aliases
|   |-- *_prompt.txt          # finance, todo, edit, and date prompts
|   |-- firefly_client.py
|   |-- vikunja_client.py
|   |-- reports.py
|   `-- attachment.py
|-- openwebui/pipelines/      # optional Open WebUI pipeline
|-- scripts/
|   |-- install.sh            # installer scaffold
|   `-- backup.sh             # encrypted backup helper
`-- docs/
    |-- INSTALL.md
    |-- CUSTOMIZATION.md
    `-- OPERATIONS.md
```

## Requirements

- Docker Engine with the Docker Compose plugin
- A Telegram bot token from BotFather
- A machine with enough memory/disk for your chosen Ollama model
- Firefly III and Vikunja API tokens, created after their first-run setup

Compass currently assumes a small private deployment. It is not intended as a
multi-tenant SaaS app.

## Quick Start

Clone the repository and create your environment file:

```bash
cp .env.example .env
```

Edit `.env` before starting services:

- Replace all image `replace-with-tested-tag` placeholders.
- Set strong database passwords.
- Set `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`.
- Generate and set `FIREFLY_APP_KEY`.

Generate a Firefly app key after setting `FIREFLY_IMAGE`:

```bash
docker run --rm "$(sed -n 's/^FIREFLY_IMAGE=//p' .env)" php artisan key:generate --show
```

Start the core services:

```bash
docker compose up -d firefly vikunja ollama
```

Open Firefly and Vikunja on the configured host ports, finish first-run setup,
create API tokens, then add them to `.env`:

- `FIREFLY_TOKEN`
- `VIKUNJA_TOKEN`

Pull the configured Ollama model:

```bash
docker compose exec ollama ollama pull "$(sed -n 's/^OLLAMA_MODEL=//p' .env)"
```

Start the bot:

```bash
docker compose up -d --build compass_bot
```

Send `/start` to your Telegram bot from an allowed Telegram user ID.

For a step-by-step version, read [docs/INSTALL.md](docs/INSTALL.md).

## Configuration

The root `.env` file controls the stack. Important settings:

| Setting | Purpose |
| --- | --- |
| `HOST_BIND` | Host address for web ports. Defaults to `127.0.0.1`. |
| `TELEGRAM_TOKEN` | Bot token from BotFather. |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated Telegram user IDs allowed to use the bot. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Optional group chat allowlist. Private chats work without this. |
| `FIREFLY_APP_KEY` | Firefly application key. |
| `FIREFLY_TOKEN` | Firefly personal access token used by the bot. |
| `VIKUNJA_TOKEN` | Vikunja API token used by the bot. |
| `OLLAMA_MODEL` | Local model used for parsing. |
| `OLLAMA_WARMUP_TIMEOUT` | Timeout for one-time startup prompt warmup. |
| `TZ` | Timezone for scheduled digest/reminder jobs. |
| `DIGEST_TIME` | Daily task digest time (`HH:MM`). Empty disables it. |
| `REMINDER_TIMES` | Optional comma-separated nudge times, e.g. `12:00,17:00,22:00`. Off by default. |

By default, web ports bind to localhost. If you change `HOST_BIND` to expose
services on a network, protect the host with a firewall, VPN, or reverse proxy.

## Customizing Accounts

Compass resolves short account aliases into exact Firefly account names. The
public repository ships with a generic [bot/accounts.py](bot/accounts.py).

For your own deployment, create `bot/accounts_local.py`:

```python
ACCOUNTS = {
    "Main Checking": ["main", "checking", "bank"],
    "Rewards Card": ["rewards", "credit card"],
    "Cash": ["cash", "wallet"],
}
```

`accounts_local.py` is ignored by git and loaded automatically when present.
The canonical account names must match Firefly exactly.

See [docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md) for more detail.

## Customizing Prompts

The parsing behavior lives in prompt files under [bot/](bot/):

- [bot/system_prompt.txt](bot/system_prompt.txt): finance transactions
- [bot/todo_prompt.txt](bot/todo_prompt.txt): todos and reminders
- [bot/edit_prompt.txt](bot/edit_prompt.txt): transaction corrections
- [bot/date_prompt.txt](bot/date_prompt.txt): defer-date parsing

The default finance prompt expects tags like `firm` and `personal`. If you
change that schema, also review report filtering in [bot/reports.py](bot/reports.py).

## Telegram Commands

Core commands:

```text
/start
/help
/balances
/categories
/today
/yesterday
/thisweek
/thismonth
/tasks
/search <keyword>
/edit <transaction-id>
```

Report commands support optional filters:

```text
/today firm
/thismonth personal
/tasks work
/tasks personal
```

Most normal use does not require commands. Send plain messages for capture:

```text
uber 340 airport checking personal
salary 150000 from acme to savings
paid credit card 25000 from main bank to rewards card
remind me to file GST by friday
remind me to pay card bill every 5th
```

Compass will show a Telegram confirmation card before creating anything.

## Attachments

For transaction records, tap `Attach File` on the confirmation card and send PDF
or image receipts. Supported formats:

- PDF
- JPG/JPEG
- PNG
- WEBP

Files are kept locally only while pending. Successful uploads are deleted from
the bot attachment directory. Failed uploads remain retryable until you retry or
discard them.

## Optional Open WebUI

Open WebUI and Pipelines are available behind the `ai-webui` Compose profile:

```bash
docker compose --profile ai-webui up -d
```

This is optional and not required for the Telegram bot.

## Operations

Useful commands:

```bash
docker compose ps
docker compose logs -f compass_bot
docker compose restart compass_bot
docker compose up -d --build compass_bot
```

Runtime data lives under `data/`:

- Postgres data for Firefly and Vikunja
- Firefly uploads
- Vikunja files
- Ollama model files
- Open WebUI data
- temporary bot attachments

For backup guidance, see [docs/OPERATIONS.md](docs/OPERATIONS.md) and
[scripts/backup.sh](scripts/backup.sh).

## Security

Do not commit:

- `.env`
- `data/`
- database dumps
- Firefly uploads
- Vikunja files
- Ollama model data
- API tokens or Telegram tokens
- private `bot/accounts_local.py`

The bot only accepts updates from `TELEGRAM_ALLOWED_USER_IDS`. Group chats must
also be explicitly listed in `TELEGRAM_ALLOWED_CHAT_IDS`.

Read [SECURITY.md](SECURITY.md) before publishing a fork or exposing the web UIs.

## Installer Status

[scripts/install.sh](scripts/install.sh) is an installer scaffold. It currently:

- checks Docker and Docker Compose
- creates `.env` from `.env.example` if needed
- creates runtime directories
- refuses to proceed while required image placeholders remain
- prints the remaining manual setup commands

The long-term goal is a single-line installer that can prepare the full stack.

## Running Tests

The test suite runs entirely offline — Firefly, Vikunja, Ollama, and Telegram
are mocked, and the backup script is exercised against stub binaries. No
credentials are needed.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

## Development Checks

The basic local validation commands are:

```bash
python3 -m py_compile bot/*.py openwebui/pipelines/*.py
docker compose --env-file .env.example config --quiet
bash -n scripts/install.sh scripts/backup.sh bot/test.sh bot/test_todo.sh
docker build -t compass_bot:review ./bot
```

Remove the temporary review image after a build check:

```bash
docker image rm compass_bot:review
```

## Project Status

Compass is public-repo ready as a self-hosted template, but still early. Before
using it for important data, test the full flow against your own Firefly and
Vikunja instances:

- transaction create/edit
- card payment handling
- receipt attachment upload and retry
- todo creation
- task defer/done/delete actions
- report commands
- backup and restore

Contributions should keep private data out of the repository and preserve the
local-first deployment model.
