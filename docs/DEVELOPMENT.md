# Development

## Running Tests

The test suite runs entirely offline — Firefly, Vikunja, Ollama, and Telegram
are mocked, and the backup script is exercised against stub binaries. No
credentials are needed.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

## Local Checks

The basic validation commands before a pull request:

```bash
python3 -m py_compile bot/*.py openwebui/pipelines/*.py
docker compose --env-file .env.example config --quiet
bash -n install.sh scripts/backup.sh bot/test.sh bot/test_todo.sh
docker build -t compass_bot:review ./bot
```

Remove the temporary review image after a build check:

```bash
docker image rm compass_bot:review
```

## Contribution Ground Rules

- Keep private data out of the repository: no real tokens, account names,
  database dumps, or anything from `data/`. Secrets belong only in `.env`,
  `bot/accounts_local.py`, and `.backup_passphrase` (all gitignored).
- Preserve the local-first deployment model — no feature should require a
  hosted service or send message content off the machine.
- Live-test flows that touch Firefly/Vikunja against your own instances
  before submitting: transaction create/edit, card payment handling, receipt
  attachment upload and retry, todo creation, task defer/done/delete,
  report commands, backup and restore.
