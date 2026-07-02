"""
Reports module: fetches transactions and balances from Firefly III
and formats them for Telegram display.
"""

import os
from datetime import date, datetime, timedelta
from collections import defaultdict
import pytz
from telegram.helpers import escape_markdown
from firefly_client import FireflyClient

MAX_DETAIL_TXNS = 20  # Cap on per-section detail before "...and N more"
REPORT_TIMEZONE = pytz.timezone(os.environ.get("TZ", "Asia/Kolkata"))


def md(value) -> str:
    return escape_markdown(str(value), version=1)


# ---------- Date helpers ----------

def date_range(period: str):
    """Return (start_date, end_date, human_label) for a period keyword."""
    today = datetime.now(REPORT_TIMEZONE).date()
    if period == "today":
        return today, today, today.strftime("%d %b %Y")
    if period == "yesterday":
        d = today - timedelta(days=1)
        return d, d, d.strftime("%d %b %Y")
    if period == "thisweek":
        start = today - timedelta(days=today.weekday())  # Monday
        return start, today, f"{start.strftime('%d %b')} – {today.strftime('%d %b %Y')}"
    if period == "thismonth":
        start = today.replace(day=1)
        return start, today, today.strftime("%B %Y")
    raise ValueError(f"Unknown period: {period}")


# ---------- Balance report ----------

def balances(client: FireflyClient) -> str:
    client._ensure_cache(refresh=True)
    cache = client._account_cache  # {name: {id, type}}

    # Fetch detailed balance info per account
    bank_lines = []
    card_lines = []
    bank_total = 0.0
    card_total = 0.0

    for name, info in sorted(cache.items()):
        resp = client._request(
            "GET",
            f"/api/v1/accounts/{info['id']}",
            action=f"Fetch account {name}",
            timeout=10,
        )
        attrs = resp.json()["data"]["attributes"]
        balance = float(attrs.get("current_balance") or 0)
        line = f"  `{md(name):<22}` ₹{balance:>12,.2f}"

        if info["type"] == "asset":
            bank_lines.append(line)
            bank_total += balance
        else:  # liability
            card_lines.append(line)
            card_total += balance

    out = ["💰 *Account Balances*", ""]
    out.append("🏦 *Bank Accounts & Cash*")
    out.extend(bank_lines)
    out.append(f"  *Total*                ₹{bank_total:>12,.2f}")
    out.append("")
    out.append("💳 *Credit Cards*")
    out.extend(card_lines)
    out.append(f"  *Total*                ₹{card_total:>12,.2f}")
    out.append("")
    out.append(f"📊 *Net Worth*           ₹{bank_total + card_total:>12,.2f}")
    return "\n".join(out)


# ---------- Categories report ----------

def categories(client: FireflyClient) -> str:
    resp = client._request(
        "GET",
        "/api/v1/categories",
        action="Fetch categories",
        params={"limit": 100},
        timeout=10,
    )
    names = sorted(c["attributes"]["name"] for c in resp.json()["data"])
    out = [f"📁 *Categories* ({len(names)})", ""]
    out.extend(f"  • {md(n)}" for n in names)
    return "\n".join(out)


# ---------- Transaction report ----------

def _fetch_transactions(client: FireflyClient, start: date, end: date) -> list:
    """Fetch all transactions in a date range. Handles pagination."""
    all_txns = []
    page = 1
    while True:
        resp = client._request(
            "GET",
            "/api/v1/transactions",
            action="Fetch transactions",
            params={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 100,
                "page": page,
            },
            timeout=15,
        )
        body = resp.json()
        for entry in body.get("data", []):
            for t in entry["attributes"]["transactions"]:
                all_txns.append(t)
        meta = body.get("meta", {}).get("pagination", {})
        if page >= meta.get("total_pages", 1):
            break
        page += 1
    return all_txns


def _format_txn_line(txn: dict, sign: str) -> str:
    """One line for a transaction. sign is '+' or '-'."""
    when = datetime.fromisoformat(txn["date"].replace("Z", "+00:00")).strftime("%H:%M")
    amt = f"{sign}₹{float(txn['amount']):,.0f}"
    src = txn.get("source_name") or "?"
    dst = txn.get("destination_name") or "?"
    cat = txn.get("category_name") or "?"
    tags = txn.get("tags") or []
    tag_str = "/".join(tags) if tags else "untagged"
    return f"`{when}` {amt:>10}  {md(src)} → {md(dst)}  _[{md(cat)}/{md(tag_str)}]_"


def transactions(client: FireflyClient, period: str, tag_filter: str = None) -> str:
    start, end, label = date_range(period)
    txns = _fetch_transactions(client, start, end)

    # Optional tag filter
    if tag_filter:
        txns = [t for t in txns if tag_filter in (t.get("tags") or [])]

# Bucket by direction.
    # Special case: Card Payment and Cash Movement are stored as withdrawals
    # in Firefly (because asset→liability must be a withdrawal), but they're
    # conceptually transfers — they don't change net worth.
    INTERNAL_CATEGORIES = {"Card Payment", "Cash Movement"}

    income = [t for t in txns if t["type"] == "deposit"]
    expense = [t for t in txns if t["type"] == "withdrawal"
               and t.get("category_name") not in INTERNAL_CATEGORIES]
    transfer = [t for t in txns if t["type"] == "transfer"
                or (t["type"] == "withdrawal"
                    and t.get("category_name") in INTERNAL_CATEGORIES)]

    income_total = sum(float(t["amount"]) for t in income)
    expense_total = sum(float(t["amount"]) for t in expense)
    transfer_total = sum(float(t["amount"]) for t in transfer)

    header = f"📅 *{label}*"
    if tag_filter:
        header += f" — _{tag_filter}_"
    out = [header, ""]

    # ---- Income ----
    if income:
        out.append(f"💰 *Income (₹{income_total:,.0f})*")
        for t in income[:MAX_DETAIL_TXNS]:
            out.append(_format_txn_line(t, "+"))
        if len(income) > MAX_DETAIL_TXNS:
            out.append(f"  _…and {len(income) - MAX_DETAIL_TXNS} more_")
        out.append("")

    # ---- Expenses ----
    if expense:
        out.append(f"💸 *Expenses (₹{expense_total:,.0f})*")
        # Category summary
        by_cat = defaultdict(float)
        for t in expense:
            by_cat[t.get("category_name") or "Uncategorized"] += float(t["amount"])
        out.append("  _By category:_")
        for cat, total in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            out.append(f"    {md(cat)}: ₹{total:,.0f}")
        out.append("")
        # Detail
        out.append("  _Recent:_")
        for t in expense[:MAX_DETAIL_TXNS]:
            out.append(_format_txn_line(t, "-"))
        if len(expense) > MAX_DETAIL_TXNS:
            out.append(f"  _…and {len(expense) - MAX_DETAIL_TXNS} more_")
        out.append("")

    # ---- Transfers ----
    if transfer:
        out.append(f"🔀 *Transfers (₹{transfer_total:,.0f})*")
        for t in transfer[:MAX_DETAIL_TXNS]:
            out.append(_format_txn_line(t, "↔"))
        if len(transfer) > MAX_DETAIL_TXNS:
            out.append(f"  _…and {len(transfer) - MAX_DETAIL_TXNS} more_")
        out.append("")

    # ---- Net ----
    net = income_total - expense_total
    out.append(f"📊 *Net*: {'+' if net >= 0 else ''}₹{net:,.0f}")

    if not (income or expense or transfer):
        return f"{header}\n\n_No transactions in this period._"

    return "\n".join(out)
