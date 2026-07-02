# Install

## Prerequisites

- Docker Engine with the Compose plugin
- A Telegram bot token from BotFather
- Enough disk and memory for Ollama and your chosen model

## 1. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill:

- `TELEGRAM_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `FIREFLY_APP_KEY`
- `FIREFLY_DB_PASSWORD`
- `VIKUNJA_DB_PASSWORD`
- `PIPELINES_API_KEY`

Set fixed image tags in `.env` before starting services. The template uses
`replace-with-tested-tag` placeholders for images that should not float.

By default, service ports bind to `127.0.0.1`. Change `HOST_BIND` only if you
intend to expose the web UIs beyond the local machine.

Generate a Firefly app key:

```bash
docker run --rm "$(sed -n 's/^FIREFLY_IMAGE=//p' .env)" php artisan key:generate --show
```

## 2. Start Core Services

```bash
docker compose up -d firefly vikunja ollama
```

Open Firefly and Vikunja on the configured host ports and finish their first-run setup.
Create API tokens in both applications, then add them to `.env`:

- `FIREFLY_TOKEN`
- `VIKUNJA_TOKEN`

Pull your Ollama model:

```bash
docker compose exec ollama ollama pull "$(sed -n 's/^OLLAMA_MODEL=//p' .env)"
```

## 3. Customize Accounts And Prompts

Update [bot/accounts.py](../bot/accounts.py) to match your Firefly account names.
Then review the prompt files in [bot/](../bot/) so categories, tags, and examples
match your use case.

## 4. Start The Bot

```bash
docker compose up -d --build compass_bot
```

Send `/start` to your Telegram bot from an allowed user ID.

## 5. Optional Open WebUI

```bash
docker compose --profile ai-webui up -d
```

Open the configured `OPEN_WEBUI_PORT` and create the first admin account.
