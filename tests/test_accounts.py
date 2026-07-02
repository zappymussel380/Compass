import pytest

import accounts
from accounts import resolve_account

# The map is deployment-specific (accounts_local.py overrides it when present),
# so every test pins a known map instead of relying on whichever one loaded.
TEST_ACCOUNTS = {
    "Primary Checking": ["checking", "main bank", "bank"],
    "Bank A": ["shared"],
    "Bank B": ["shared"],
    "Cash": ["cash", "wallet"],
}


@pytest.fixture(autouse=True)
def pinned_accounts(monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS", TEST_ACCOUNTS)
    monkeypatch.setattr(accounts, "_REVERSE", accounts._build_reverse_index())


def test_exact_alias_matches():
    assert resolve_account("checking") == ("match", "Primary Checking")


def test_multiword_alias():
    assert resolve_account("main bank") == ("match", "Primary Checking")


def test_case_and_whitespace_insensitive():
    assert resolve_account("  CASH  ") == ("match", "Cash")


def test_unknown_alias():
    assert resolve_account("garbage") == ("unknown", None)


def test_empty_input():
    assert resolve_account("") == ("unknown", None)
    assert resolve_account(None) == ("unknown", None)


def test_ambiguous_alias():
    status, names = resolve_account("shared")
    assert status == "ambiguous"
    assert sorted(names) == ["Bank A", "Bank B"]
