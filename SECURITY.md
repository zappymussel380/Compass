# Security

## Posture

- **No secrets in the repository.** All tokens, passwords, and keys live in
  `.env` (gitignored). `.env.example` contains placeholders only.
- **Telegram authorization.** Every update — messages, commands, files, and
  button taps — is checked against `TELEGRAM_ALLOWED_USER_IDS`. Group chats
  are refused unless the chat ID is also listed in
  `TELEGRAM_ALLOWED_CHAT_IDS`. Scheduled digests are sent only to allowed
  user IDs.
- **Local-only network exposure by default.** All published web ports bind to
  `127.0.0.1` through `HOST_BIND`. Do not set `HOST_BIND=0.0.0.0` unless the
  host is protected by a firewall, VPN, or a properly configured reverse
  proxy. Set `FIREFLY_TRUSTED_PROXIES` only to proxy addresses you control.
- **Non-root bot container.** The bot runs as UID 1000; the attachments
  volume must be writable by that UID (see docs/OPERATIONS.md).
- **Receipt handling.** Attachment files are staged on disk only while a
  transaction is pending, are tied to that specific transaction, are deleted
  immediately after successful upload to Firefly, and any stale staging files
  are purged on startup.
- **Backups.** `scripts/backup.sh` encrypts all dumps with GPG (AES256)
  before they leave the machine. The passphrase is read from a file
  (`--passphrase-file`), never passed on the command line, and remote
  retention cleanup only touches `*.gpg` files.
- **Logs.** Errors are logged with exception detail but tokens are never
  interpolated into log messages. Firefly/Vikunja error bodies are truncated
  before being echoed back to the (already authorized) chat.

## Do not publish

- `.env` or any real tokens (Telegram, Firefly, Vikunja)
- `data/` (databases, uploads, model files)
- database dumps or backup archives
- your private `bot/accounts_local.py`
- your `.backup_passphrase`

## Known limitations

- Pending confirmations and queued attachments are held in memory; a restart
  drops them (files are cleaned up, nothing is written to external services
  without an explicit Confirm).
- The bot trusts the Firefly and Vikunja instances it is pointed at; run
  them on the same private network (the default Compose setup does).
- LLM parsing runs locally via Ollama; no message content leaves the host.

## Reporting

If a secret is accidentally committed, revoke and rotate it before pushing.
To report a vulnerability, open a GitHub issue (avoid including exploit
details in public; ask for a private contact channel if needed).
