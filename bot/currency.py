"""Deployment currency: one ISO 4217 code for the whole instance.

Set CURRENCY in .env (default INR). Firefly stores the currency on every
transaction; this module only controls what the bot displays and what the
LLM prompts are told to emit.
"""

import os

CODE = os.environ.get("CURRENCY", "INR").strip().upper() or "INR"

_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "KRW": "₩",
    "RUB": "₽",
    "TRY": "₺",
    "VND": "₫",
    "ILS": "₪",
    "NGN": "₦",
    "PHP": "₱",
    "THB": "฿",
    "AUD": "A$",
    "CAD": "C$",
    "SGD": "S$",
    "HKD": "HK$",
    "NZD": "NZ$",
    "CHF": "CHF ",
    "SEK": "kr ",
    "NOK": "kr ",
    "DKK": "kr ",
    "AED": "AED ",
    "BRL": "R$",
    "MXN": "MX$",
    "ZAR": "R ",
}

# Fall back to "CODE " so unknown currencies still render unambiguously.
SYMBOL = _SYMBOLS.get(CODE, f"{CODE} ")


def fmt_amount(value) -> str:
    """1,250 for whole amounts, 1,250.50 otherwise (no trailing .0)."""
    value = float(value)
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def money(value) -> str:
    """Symbol-prefixed amount: ₹1,250 / $1,250.50."""
    return f"{SYMBOL}{fmt_amount(value)}"
