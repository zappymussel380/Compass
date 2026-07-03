"""
Account resolver: maps fuzzy user-typed aliases to canonical Firefly III account names.

Resolution returns one of:
  ('match', canonical_name)             — single unambiguous match
  ('ambiguous', [name1, name2, ...])    — alias is ambiguous, bot must ask
  ('unknown', None)                     — nothing matched

Each bot user gets their own AccountResolver built from their users/<id>.json
"accounts" map, so one person's bank names never leak into another's picker.

The module-level ACCOUNTS map below is the legacy single-user layout: public
clones ship the generic map, and a private deployment may override it with a
gitignored bot/accounts_local.py. It is used only as the migration source for
deployments that predate per-user config.
"""

ACCOUNTS = {
    "Primary Checking": ["checking", "main bank", "bank", "salary"],
    "Savings Account": ["savings", "save"],
    "Business Checking": ["business", "firm", "office account"],
    "Travel Card": ["travel card", "travel", "visa"],
    "Rewards Card": ["rewards card", "rewards", "credit card"],
    "Cash": ["cash", "wallet"],
}

try:
    import accounts_local
except ModuleNotFoundError as exc:
    if exc.name != "accounts_local":
        raise
else:
    ACCOUNTS = accounts_local.ACCOUNTS


class AccountResolver:
    """Alias → canonical account resolution over one user's account map."""

    def __init__(self, accounts: dict[str, list[str]]):
        self.accounts = accounts
        # Stable, sorted list so picker buttons can reference accounts by
        # index — full names would overflow Telegram's 64-byte callback_data.
        self.choices = sorted(accounts)
        self._reverse: dict[str, list[str]] = {}
        for canonical, aliases in accounts.items():
            for alias in aliases:
                self._reverse.setdefault(alias.strip().lower(), []).append(canonical)

    def resolve(self, raw: str):
        if not raw:
            return ("unknown", None)
        matches = self._reverse.get(raw.strip().lower())
        if not matches:
            return ("unknown", None)
        if len(matches) == 1:
            return ("match", matches[0])
        return ("ambiguous", matches)


_LEGACY = AccountResolver(ACCOUNTS)


def resolve_account(raw: str):
    """Legacy helper resolving against the module-level ACCOUNTS map."""
    return _LEGACY.resolve(raw)


# Quick self-test — run `python3 accounts.py` to verify
if __name__ == "__main__":
    cases = [
        "checking",     # match → Primary Checking
        "travel",       # match → Travel Card
        "business",     # match → Business Checking
        "rewards",      # match → Rewards Card
        "garbage",      # unknown
        "  CASH  ",     # match → Cash (whitespace + case insensitive)
    ]
    for c in cases:
        print(f"{c!r:20} → {resolve_account(c)}")
