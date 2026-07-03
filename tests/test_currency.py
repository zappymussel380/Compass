"""Deployment-currency module and prompt parameterization."""

import importlib
import os

import currency


BOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bot"))


def reload_with(code, monkeypatch):
    if code is None:
        monkeypatch.delenv("CURRENCY", raising=False)
    else:
        monkeypatch.setenv("CURRENCY", code)
    return importlib.reload(currency)


def test_default_is_inr(monkeypatch):
    cur = reload_with(None, monkeypatch)
    assert cur.CODE == "INR"
    assert cur.SYMBOL == "₹"


def test_known_symbols(monkeypatch):
    assert reload_with("usd", monkeypatch).SYMBOL == "$"  # normalized upper
    assert reload_with("EUR", monkeypatch).SYMBOL == "€"
    assert reload_with("GBP", monkeypatch).SYMBOL == "£"


def test_unknown_code_falls_back_to_code_prefix(monkeypatch):
    cur = reload_with("XXX", monkeypatch)
    assert cur.SYMBOL == "XXX "
    assert cur.money(5) == "XXX 5"


def test_money_formatting(monkeypatch):
    cur = reload_with("INR", monkeypatch)
    assert cur.money(100) == "₹100"
    assert cur.money(100.0) == "₹100"          # F-2: no trailing .0
    assert cur.money(1250.5) == "₹1,250.50"
    assert cur.money("2500") == "₹2,500"


def test_prompts_are_parameterized_not_hardcoded():
    for fname in ("system_prompt.txt", "edit_prompt.txt"):
        text = open(os.path.join(BOT_DIR, fname)).read()
        assert "{currency}" in text, f"{fname} lost its placeholder"
        assert "INR" not in text, f"{fname} still hardcodes INR"


def teardown_module():
    # Later tests import modules that read currency at import time; make sure
    # the reloaded state matches the conftest default (no CURRENCY set).
    os.environ.pop("CURRENCY", None)
    importlib.reload(currency)
