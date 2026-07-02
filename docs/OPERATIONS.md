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

- `BACKUP_DIR` — where encrypted archives are written (default `./backups`)
- `PASSPHRASE_FILE` — file containing the GPG passphrase
  (default `.backup_passphrase`, gitignored; `chmod 600` it)
- `RCLONE_REMOTE` — optional rclone destination for offsite copies
- `RETENTION_DAYS` — how long to keep old backups (default 30)

Run:

```bash
scripts/backup.sh
```

## Scheduled Messages

Two kinds of scheduled Telegram messages exist, both sent only to
`TELEGRAM_ALLOWED_USER_IDS` in the `TZ` timezone:

- `DIGEST_TIME` (default `11:00`) — daily overdue/due-today task digest.
  Set it empty to disable.
- `REMINDER_TIMES` (default empty) — optional comma-separated "log your
  expenses" nudges, e.g. `12:00,17:00,22:00`.

## Attachments Volume Ownership

The bot container runs as UID 1000 and stages receipt files in
`data/bot_attachments`. If the bot logs permission errors when saving files,
make the directory writable by that UID:

```bash
sudo chown -R 1000 data/bot_attachments
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
