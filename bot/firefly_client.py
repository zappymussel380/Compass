"""
Firefly III API client.
Wraps the subset of Firefly's REST API the bot actually uses.
"""

import os
import requests
from datetime import datetime

import currency


class FireflyError(Exception):
    """Raised when Firefly returns an error or is unreachable."""
    pass


class FireflyClient:
    def __init__(self, base_url: str = None, token: str = None):
        self.base_url = (base_url or os.environ["FIREFLY_URL"]).rstrip("/")
        self.token = token or os.environ["FIREFLY_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._account_cache = None  # populated on first lookup
        # Now stores {name: {"id": int, "type": "asset"|"liabilities"}}

    def _request(self, method: str, path: str, *, action: str, timeout: int = 15, **kwargs):
        try:
            resp = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise FireflyError(f"{action} failed: {exc}") from exc

        if not resp.ok:
            raise FireflyError(
                f"{action} failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp

    # ---------- Account lookup ----------

    def list_accounts(self) -> list[dict]:
        """All asset + liability accounts with current balances. Paginated."""
        accounts = []
        for acc_type in ("asset", "liability"):
            page = 1
            while True:
                resp = self._request(
                    "GET",
                    "/api/v1/accounts",
                    action=f"Fetch {acc_type} accounts",
                    params={"type": acc_type, "limit": 100, "page": page},
                    timeout=15,
                )
                body = resp.json()
                for entry in body.get("data", []):
                    attrs = entry["attributes"]
                    accounts.append({
                        "id": int(entry["id"]),
                        "name": attrs["name"],
                        "type": attrs["type"],
                        "current_balance": float(attrs.get("current_balance") or 0),
                    })
                meta = body.get("meta", {}).get("pagination", {})
                if page >= meta.get("total_pages", 1):
                    break
                page += 1
        return accounts

    def _fetch_all_accounts(self):
        """Build the name → {id, type} lookup cache."""
        return {
            acc["name"]: {"id": acc["id"], "type": acc["type"]}
            for acc in self.list_accounts()
        }

    def _ensure_cache(self, refresh: bool = False):
        if self._account_cache is None or refresh:
            self._account_cache = self._fetch_all_accounts()

    def get_account_id(self, name: str, refresh: bool = False) -> int:
        self._ensure_cache(refresh)
        if name not in self._account_cache:
            raise FireflyError(
                f"Account '{name}' not found in Firefly. "
                f"Known accounts: {sorted(self._account_cache.keys())}"
            )
        return self._account_cache[name]["id"]

    def is_liability(self, name: str) -> bool:
        self._ensure_cache()
        return self._account_cache.get(name, {}).get("type") in {"liability", "liabilities"}

    # ---------- Transactions ----------

    def build_transaction_payload(self, parsed: dict, source_canonical: str = None,
                                  destination_canonical: str = None) -> dict:
        """Build a Firefly transaction payload from Compass' parsed shape."""
        txn_type = parsed["type"]

        txn = {
            "date": parsed.get("date") or datetime.now().isoformat(timespec="seconds"),
            "amount": str(parsed["amount"]),
            "currency_code": parsed.get("currency") or currency.CODE,
            "description": parsed["description"],
            "category_name": parsed.get("category"),
            "tags": parsed.get("tags", []),
        }

        if txn_type == "withdrawal":
            if not source_canonical:
                raise FireflyError("Withdrawals require source_canonical")
            txn["type"] = "withdrawal"
            txn["source_id"] = self.get_account_id(source_canonical)
            txn["destination_name"] = parsed.get("destination_raw") or "Unknown"

        elif txn_type == "transfer":
            if not source_canonical or not destination_canonical:
                raise FireflyError(
                    "Transfers require both source and destination canonical names"
                )
            txn["source_id"] = self.get_account_id(source_canonical)
            txn["destination_id"] = self.get_account_id(destination_canonical)

            # Firefly quirk: asset→liability is not a transfer, it's a withdrawal.
            if self.is_liability(destination_canonical):
                txn["type"] = "withdrawal"
            else:
                txn["type"] = "transfer"

        elif txn_type == "deposit":
            if not destination_canonical:
                raise FireflyError("Deposits require destination_canonical")
            txn["type"] = "deposit"
            # Source = external payer; let Firefly auto-create the revenue account.
            txn["source_name"] = (parsed.get("source_raw") or "Unknown").title()
            txn["destination_id"] = self.get_account_id(destination_canonical)

        else:
            raise FireflyError(f"Unsupported transaction type: {txn_type}")

        return {"transactions": [txn]}

    def create_transaction(self, parsed: dict, source_canonical: str = None,
                           destination_canonical: str = None) -> dict:
        """
        Push a parsed transaction into Firefly.

        Firefly quirk: transfers can only happen between two asset accounts.
        If the user's intent is a transfer (e.g. paying a credit card bill from
        a bank), but the destination is a liability, we transparently re-cast
        the call as a withdrawal — Firefly's required transaction type for
        asset→liability movement.
        """
        payload = self.build_transaction_payload(
            parsed,
            source_canonical=source_canonical,
            destination_canonical=destination_canonical,
        )
        resp = self._request(
            "POST",
            "/api/v1/transactions",
            action="Transaction create",
            json=payload,
            timeout=15,
        )
        return resp.json()["data"]

    def search_transactions(self, query: str):
        """Search transactions using keywords in notes/description."""
        resp = self._request(
            "GET",
            "/api/v1/search/transactions",
            action="Transaction search",
            params={"query": query, "limit": 5},
            timeout=10,
        )
        return resp.json()["data"]

    def get_transaction(self, txn_id: int):
        """Fetch a specific transaction's details."""
        resp = self._request(
            "GET",
            f"/api/v1/transactions/{txn_id}",
            action="Transaction fetch",
            timeout=10,
        )
        return resp.json()["data"]

    def update_transaction(self, txn_id: int, data: dict):
        """Update an existing transaction."""
        resp = self._request(
            "PUT",
            f"/api/v1/transactions/{txn_id}",
            action="Transaction update",
            json=data,
            timeout=15,
        )
        return resp.json()["data"]

# ---------- Self-test ----------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = FireflyClient()
    accounts = client.list_accounts()
    print(f"Connected. Found {len(accounts)} accounts:")
    for acc in sorted(accounts, key=lambda a: a["name"]):
        print(f"  {acc['id']:>3}  {acc['type']:>11}  {acc['name']}")
