"""
Account resolver: maps fuzzy user-typed aliases to canonical Firefly III account names.
Returns one of:
  ('match', canonical_name)             — single unambiguous match
  ('ambiguous', [name1, name2, ...])    — alias is ambiguous, bot must ask
  ('unknown', None)                     — nothing matched
"""

# Canonical name → list of aliases (all lowercase).
# Public clones use this generic map. For a private deployment, create
# bot/accounts_local.py with ACCOUNTS = {...}; it is ignored by git and loaded
# automatically.
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

def _build_reverse_index():
    """Build alias → [canonical_names] map. Multi-valued entries = ambiguous."""
    index = {}
    for canonical, aliases in ACCOUNTS.items():
        for alias in aliases:
            index.setdefault(alias, []).append(canonical)
    return index


_REVERSE = _build_reverse_index()


def resolve_account(raw: str):
    """
    Resolve a raw alias string to a canonical account name.
    Returns (status, value):
        ('match', 'Primary Checking')
        ('ambiguous', ['Primary Checking', 'Business Checking'])
        ('unknown', None)
    """
    if not raw:
        return ('unknown', None)

    key = raw.strip().lower()
    matches = _REVERSE.get(key)

    if not matches:
        return ('unknown', None)
    if len(matches) == 1:
        return ('match', matches[0])
    return ('ambiguous', matches)


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
