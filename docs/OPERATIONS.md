# Operations

## Common Commands

```bash
docker compose ps
docker compose logs -f compass_bot
docker compose restart compass_bot
docker compose up -d --build compass_bot
```

Optional Open WebUI:

```bash
docker compose --profile ai-webui up -d
```

## Backups

The optional [scripts/backup.sh](../scripts/backup.sh) creates encrypted database and
configuration backups. Configure these environment variables before use:

- `BACKUP_DIR`
- `PASSPHRASE_FILE`
- `RCLONE_REMOTE` optional

Run:

```bash
scripts/backup.sh
```

## Telegram Privacy

The bot checks `TELEGRAM_ALLOWED_USER_IDS` for every update. It replies in private
chats by default. To use a group, set `TELEGRAM_ALLOWED_CHAT_IDS` explicitly.

## Runtime Data

Runtime data is stored under `data/` and ignored by git:

- Postgres databases
- Firefly uploads
- Vikunja files
- Ollama model files
- Open WebUI data
- bot attachment temp files
