import pytest

from conftest import FakeResponse
from firefly_client import FireflyClient, FireflyError


@pytest.fixture
def client():
    c = FireflyClient(base_url="http://firefly.test", token="t")
    c._account_cache = {
        "Primary Checking": {"id": 1, "type": "asset"},
        "Savings Account": {"id": 2, "type": "asset"},
        "Rewards Card": {"id": 3, "type": "liabilities"},
        "Cash": {"id": 4, "type": "asset"},
    }
    return c


# ---------- build_transaction_payload ----------

def test_withdrawal_payload(client):
    parsed = {
        "type": "withdrawal", "amount": 250.0, "currency": "INR",
        "source_raw": "checking", "destination_raw": "Swiggy",
        "category": "Food and Drinks", "tags": ["personal"],
        "description": "Swiggy lunch", "date": "2026-07-01",
    }
    payload = client.build_transaction_payload(parsed, source_canonical="Primary Checking")
    txn = payload["transactions"][0]
    assert txn["type"] == "withdrawal"
    assert txn["source_id"] == 1
    assert txn["destination_name"] == "Swiggy"
    assert txn["amount"] == "250.0"
    assert txn["date"] == "2026-07-01"


def test_withdrawal_requires_source(client):
    with pytest.raises(FireflyError, match="source_canonical"):
        client.build_transaction_payload(
            {"type": "withdrawal", "amount": 1, "description": "x"},
            source_canonical=None,
        )


def test_transfer_to_liability_recast_as_withdrawal(client):
    """Card bill payments must become withdrawals: Firefly rejects
    asset->liability transfers."""
    parsed = {
        "type": "transfer", "amount": 15000, "description": "Card bill",
        "category": "Card Payment", "tags": ["personal"],
    }
    payload = client.build_transaction_payload(
        parsed, source_canonical="Primary Checking", destination_canonical="Rewards Card",
    )
    txn = payload["transactions"][0]
    assert txn["type"] == "withdrawal"
    assert txn["source_id"] == 1
    assert txn["destination_id"] == 3


def test_transfer_between_assets_stays_transfer(client):
    parsed = {"type": "transfer", "amount": 5000, "description": "ATM",
              "category": "Cash Movement", "tags": ["personal"]}
    payload = client.build_transaction_payload(
        parsed, source_canonical="Savings Account", destination_canonical="Cash",
    )
    assert payload["transactions"][0]["type"] == "transfer"


def test_deposit_payload_titlecases_payer(client):
    parsed = {"type": "deposit", "amount": 50000, "description": "Fees",
              "source_raw": "client", "category": "Professional Fees", "tags": ["firm"]}
    payload = client.build_transaction_payload(parsed, destination_canonical="Primary Checking")
    txn = payload["transactions"][0]
    assert txn["type"] == "deposit"
    assert txn["source_name"] == "Client"
    assert txn["destination_id"] == 1


def test_unknown_type_rejected(client):
    with pytest.raises(FireflyError, match="Unsupported transaction type"):
        client.build_transaction_payload({"type": "magic", "amount": 1, "description": "x"})


def test_unknown_account_lists_known_names(client):
    with pytest.raises(FireflyError, match="not found in Firefly"):
        client.get_account_id("Nonexistent")


# ---------- account pagination ----------

def test_list_accounts_walks_pages(monkeypatch, client):
    pages = {
        ("asset", 1): FakeResponse({
            "data": [{"id": "1", "attributes": {"name": "A", "type": "asset", "current_balance": "10"}}],
            "meta": {"pagination": {"total_pages": 2}},
        }),
        ("asset", 2): FakeResponse({
            "data": [{"id": "2", "attributes": {"name": "B", "type": "asset", "current_balance": None}}],
            "meta": {"pagination": {"total_pages": 2}},
        }),
        ("liability", 1): FakeResponse({
            "data": [{"id": "3", "attributes": {"name": "C", "type": "liabilities", "current_balance": "-5"}}],
            "meta": {"pagination": {"total_pages": 1}},
        }),
    }

    def fake_request(method, path, *, action, timeout=15, **kwargs):
        params = kwargs["params"]
        return pages[(params["type"], params["page"])]

    monkeypatch.setattr(client, "_request", fake_request)
    accounts = client.list_accounts()
    assert [a["name"] for a in accounts] == ["A", "B", "C"]
    assert accounts[1]["current_balance"] == 0.0
    assert accounts[2]["current_balance"] == -5.0


def test_is_liability(client):
    assert client.is_liability("Rewards Card") is True
    assert client.is_liability("Primary Checking") is False
    assert client.is_liability("Nonexistent") is False
