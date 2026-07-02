from datetime import date

import pytest

import reports


class StubClient:
    """Feeds canned transaction pages / account lists into reports.py."""

    def __init__(self, txn_pages=None, accounts=None):
        self._txn_pages = txn_pages or []
        self._accounts = accounts or []

    def list_accounts(self):
        return self._accounts

    def _request(self, method, path, *, action, timeout=15, **kwargs):
        page = kwargs.get("params", {}).get("page", 1)
        body = self._txn_pages[page - 1]

        class R:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        return R(body)


def _txn(t_type, amount, category="Misc", tags=None, **extra):
    base = {
        "type": t_type,
        "amount": str(amount),
        "date": "2026-07-01T10:00:00+05:30",
        "source_name": "Src",
        "destination_name": "Dst",
        "category_name": category,
        "tags": tags or [],
    }
    base.update(extra)
    return base


def _page(txns, total_pages=1):
    return {
        "data": [{"attributes": {"transactions": [t]}} for t in txns],
        "meta": {"pagination": {"total_pages": total_pages}},
    }


# ---------- date_range ----------

def test_date_range_today():
    start, end, _ = reports.date_range("today")
    assert start == end


def test_date_range_thisweek_starts_monday():
    start, end, _ = reports.date_range("thisweek")
    assert start.weekday() == 0
    assert start <= end


def test_date_range_thismonth_starts_first():
    start, _, _ = reports.date_range("thismonth")
    assert start.day == 1


def test_date_range_unknown_period():
    with pytest.raises(ValueError):
        reports.date_range("fortnight")


# ---------- transactions report ----------

def test_income_expense_and_net():
    client = StubClient(txn_pages=[_page([
        _txn("deposit", 1000, category="Income"),
        _txn("withdrawal", 300, category="Food and Drinks"),
    ])])
    out = reports.transactions(client, "today")
    assert "Income (₹1,000)" in out
    assert "Expenses (₹300)" in out
    assert "*Net*: +₹700" in out


def test_card_payment_counts_as_transfer_not_expense():
    """Asset->liability card payments are stored as withdrawals but must not
    inflate expenses."""
    client = StubClient(txn_pages=[_page([
        _txn("withdrawal", 15000, category="Card Payment"),
    ])])
    out = reports.transactions(client, "today")
    assert "Transfers (₹15,000)" in out
    assert "Expenses" not in out
    assert "*Net*: +₹0" in out


def test_tag_filter():
    client = StubClient(txn_pages=[_page([
        _txn("withdrawal", 100, tags=["firm"]),
        _txn("withdrawal", 200, tags=["personal"]),
    ])])
    out = reports.transactions(client, "today", tag_filter="firm")
    assert "₹100" in out
    assert "₹200" not in out


def test_pagination_is_followed():
    client = StubClient(txn_pages=[
        _page([_txn("withdrawal", 100)], total_pages=2),
        _page([_txn("withdrawal", 50)], total_pages=2),
    ])
    out = reports.transactions(client, "today")
    assert "Expenses (₹150)" in out


def test_empty_period():
    client = StubClient(txn_pages=[_page([])])
    out = reports.transactions(client, "today")
    assert "No transactions" in out


# ---------- balances report ----------

def test_balances_totals_and_net_worth():
    client = StubClient(accounts=[
        {"id": 1, "name": "Primary Checking", "type": "asset", "current_balance": 1000.0},
        {"id": 2, "name": "Savings Account", "type": "asset", "current_balance": 500.0},
        {"id": 3, "name": "Rewards Card", "type": "liabilities", "current_balance": -200.0},
    ])
    out = reports.balances(client)
    assert "Primary Checking" in out
    assert "₹    1,500.00" in out       # bank total
    assert "₹     -200.00" in out       # card total
    assert "₹    1,300.00" in out       # net worth
