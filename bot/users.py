"""Per-user configuration and API clients.

Each allowed Telegram user has a JSON file in USERS_DIR named
<telegram_id>.json:

    {
        "telegram_id": 123456789,
        "name": "Display Name",
        "firefly_token": "...",
        "vikunja_token": "...",
        "accounts": {"Canonical Firefly Name": ["alias", "alias2"]}
    }

The file holds that user's own API identities, so every Firefly/Vikunja
call the bot makes on their behalf is scoped to their data by the servers
themselves. Bank/card aliases are also per-user: one user's account names
never appear in another user's picker, prompt context, or resolution.

Backward compatibility: a legacy single-user deployment (flat
TELEGRAM_ALLOWED_USER_IDS + shared FIREFLY_TOKEN/VIKUNJA_TOKEN env vars +
accounts_local.py) is migrated automatically at startup — the first
allowlisted ID becomes user files' first entry, written to USERS_DIR when
possible and kept in memory regardless.
"""

import json
import logging
import os

from accounts import AccountResolver, ACCOUNTS as LEGACY_ACCOUNTS
from firefly_client import FireflyClient
from vikunja_client import VikunjaClient

log = logging.getLogger("compass.users")

USERS_DIR = os.environ.get("USERS_DIR", "/app/users")


class UserContext:
    """Everything the bot needs to act as one person."""

    def __init__(self, telegram_id: int, name: str, firefly_token: str,
                 vikunja_token: str, accounts: dict[str, list[str]]):
        self.telegram_id = telegram_id
        self.name = name
        self.firefly = FireflyClient(token=firefly_token)
        self.vikunja = VikunjaClient(token=vikunja_token)
        self.firefly_token = firefly_token
        self.resolver = AccountResolver(accounts)

    @property
    def account_choices(self) -> list[str]:
        return self.resolver.choices


def _load_user_file(path: str) -> UserContext | None:
    try:
        with open(path) as f:
            cfg = json.load(f)
        return UserContext(
            telegram_id=int(cfg["telegram_id"]),
            name=str(cfg.get("name") or cfg["telegram_id"]),
            firefly_token=cfg["firefly_token"],
            vikunja_token=cfg["vikunja_token"],
            accounts=cfg.get("accounts") or {},
        )
    except (OSError, ValueError, KeyError) as e:
        log.error(f"Ignoring invalid user file {os.path.basename(path)}: {e}")
        return None


def _legacy_env_users() -> dict[int, UserContext]:
    """Build user contexts from the pre-multi-user env layout."""
    firefly_token = os.environ.get("FIREFLY_TOKEN", "")
    vikunja_token = os.environ.get("VIKUNJA_TOKEN", "")
    raw_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    ids = [int(x.strip()) for x in raw_ids.split(",") if x.strip()]
    if not (firefly_token and vikunja_token and ids):
        return {}
    users = {}
    for tg_id in ids:
        users[tg_id] = UserContext(
            telegram_id=tg_id,
            name=f"user-{tg_id}",
            firefly_token=firefly_token,
            vikunja_token=vikunja_token,
            accounts=LEGACY_ACCOUNTS,
        )
    return users


def _materialize(users: dict[int, UserContext]) -> None:
    """Write migrated legacy users to USERS_DIR so the deployment is visibly
    on the per-user layout. Best-effort: a read-only mount only means the
    migration stays in-memory (and re-runs next start)."""
    for tg_id, u in users.items():
        path = os.path.join(USERS_DIR, f"{tg_id}.json")
        if os.path.exists(path):
            continue
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({
                    "telegram_id": tg_id,
                    "name": u.name,
                    "firefly_token": u.firefly_token,
                    "vikunja_token": u.vikunja.token,
                    "accounts": u.resolver.accounts,
                }, f, indent=2)
            log.info(f"Migrated legacy config to users/{tg_id}.json")
        except OSError as e:
            log.warning(f"Could not write users/{tg_id}.json ({e}); "
                        "running migrated config from memory")


def load_users() -> dict[int, UserContext]:
    """Load all per-user configs; migrate legacy env config when needed."""
    users: dict[int, UserContext] = {}
    if os.path.isdir(USERS_DIR):
        for fname in sorted(os.listdir(USERS_DIR)):
            if not fname.endswith(".json"):
                continue
            u = _load_user_file(os.path.join(USERS_DIR, fname))
            if u is not None:
                users[u.telegram_id] = u

    if not users:
        users = _legacy_env_users()
        if users:
            log.info(f"No user files found — migrated {len(users)} legacy "
                     "env-configured user(s)")
            if os.path.isdir(USERS_DIR):
                _materialize(users)

    for tg_id, u in users.items():
        log.info(f"Loaded user {u.name} ({tg_id}), "
                 f"{len(u.resolver.accounts)} account(s)")
    return users
