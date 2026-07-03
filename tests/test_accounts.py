from accounts import AccountResolver, resolve_account

# Each user carries their own resolver, so tests pin a known map instead of
# relying on whichever deployment map loaded.
TEST_ACCOUNTS = {
    "Primary Checking": ["checking", "main bank", "bank"],
    "Bank A": ["shared"],
    "Bank B": ["shared"],
    "Cash": ["cash", "wallet"],
}

R = AccountResolver(TEST_ACCOUNTS)


def test_exact_alias_matches():
    assert R.resolve("checking") == ("match", "Primary Checking")


def test_multiword_alias():
    assert R.resolve("main bank") == ("match", "Primary Checking")


def test_case_and_whitespace_insensitive():
    assert R.resolve("  CASH  ") == ("match", "Cash")


def test_unknown_alias():
    assert R.resolve("garbage") == ("unknown", None)


def test_empty_input():
    assert R.resolve("") == ("unknown", None)
    assert R.resolve(None) == ("unknown", None)


def test_ambiguous_alias():
    status, names = R.resolve("shared")
    assert status == "ambiguous"
    assert sorted(names) == ["Bank A", "Bank B"]


def test_choices_are_sorted_and_stable():
    assert R.choices == sorted(TEST_ACCOUNTS)


def test_per_user_isolation():
    other = AccountResolver({"Secret Bank": ["secret"]})
    assert R.resolve("secret") == ("unknown", None)
    assert other.resolve("checking") == ("unknown", None)
    assert "Secret Bank" not in R.choices


def test_legacy_module_helper_still_resolves():
    # resolve_account() resolves against the module-level map (used only as
    # the migration source); just assert it returns the tuple shape.
    status, _ = resolve_account("nonexistent-alias-xyz")
    assert status == "unknown"
