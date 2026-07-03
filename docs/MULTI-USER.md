# Multi-User

One Compass install can serve several people — a partner, family members —
with **zero cross-visibility**: each person has their own Firefly III user,
their own Vikunja user, and their own account nicknames. Nobody can see
anyone else's balances, transactions, receipts, or tasks.

## How isolation works

Isolation is enforced by the servers, not by bot logic. Every user's file in
`users/` carries their **own** Firefly and Vikunja API tokens, and the bot
uses the token of whoever sent the message. Firefly III and Vikunja both
separate users' data natively — a request with one user's token cannot read
another user's data (the server answers 401/404). Account nicknames are also
per-user: one person's bank names never appear in another person's account
picker or parsing context.

What is shared: the Telegram bot itself, the Ollama model, and the
deployment-wide `CURRENCY`. Scheduled digests and reminders are built and
sent per user, each from their own task list.

## Adding a user

```bash
./scripts/add-user.sh
```

The script asks for their display name, Telegram user ID (from
[@userinfobot](https://t.me/userinfobot)), an email, a Vikunja username, and
their bank/card accounts with aliases. It then provisions everything:

- a Firefly III user with a fresh password, an API token, your deployment's
  currency as their default, and their starter accounts;
- a Vikunja user with an API token and `Work`/`Personal` projects;
- `users/<telegram_id>.json` (gitignored, mode 600) that the bot reads.

Restart the bot (`docker compose up -d --force-recreate compass_bot`), hand
the printed credentials to the new user, and they can start texting the bot.
It must be run by the instance owner: creating Firefly users requires the
owner's `FIREFLY_TOKEN` from `.env`.

## Removing a user

```bash
./scripts/remove-user.sh
```

Lists configured users, asks which Telegram ID to remove, and — only after
you type `DELETE` — removes their Firefly user (all financial data), their
Vikunja user (all tasks), and their `users/` file. It refuses to delete the
Firefly owner.

## Migrating from a single-user install

Nothing to do. On startup, if `users/` is empty, the bot converts the legacy
configuration (`TELEGRAM_ALLOWED_USER_IDS` + `FIREFLY_TOKEN` +
`VIKUNJA_TOKEN` + `bot/accounts_local.py`) into `users/<your_id>.json`
automatically and keeps working exactly as before. The legacy env variables
are ignored once user files exist.

## Group chats

In an allowlisted group (`TELEGRAM_ALLOWED_CHAT_IDS`), each allowlisted
member's messages act on **their own** data, and a pending confirmation card
can only be confirmed, edited, or cancelled by the person who created it —
others get "Not your card."

## Limitations

- One `CURRENCY` per deployment — all users share it.
- No shared/household Vikunja projects — tasks are strictly per person.
- The daily digest goes to each user individually; there is no combined view.
- `users/*.json` files hold API tokens. They are gitignored and mode 600;
  keep them out of backups you share.
