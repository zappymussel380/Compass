# Security

Do not publish:

- `.env`
- `data/`
- database dumps
- Firefly uploads
- Vikunja files
- Ollama model data
- Open WebUI data
- Telegram, Firefly, or Vikunja tokens

The default bot authorization allows only configured Telegram user IDs and only
private chats. Set `TELEGRAM_ALLOWED_CHAT_IDS` only if you explicitly want group
chat usage.

Docker-published web ports bind to `127.0.0.1` by default through `HOST_BIND`.
Do not set `HOST_BIND=0.0.0.0` unless the host is protected by a firewall, VPN,
or a properly configured reverse proxy. Set `FIREFLY_TRUSTED_PROXIES` only to
proxy addresses you control.

If a secret is accidentally committed, revoke and rotate it before making the
repository public.
