# Customization

## Accounts

[bot/accounts.py](../bot/accounts.py) maps short user aliases to canonical Firefly
account names. The canonical names must match Firefly exactly.

For your own deployment, create `bot/accounts_local.py` and define `ACCOUNTS`
there. The bot loads that file automatically when present, and `.gitignore`
keeps it out of the public repository.

Example:

```python
"Main Bank": ["main", "bank", "checking"],
"Travel Card": ["travel card", "visa"],
```

Transfers require both accounts to resolve. Withdrawals require a source account.
Deposits require a destination account.

## Currency

One `CURRENCY` code (ISO 4217) in `.env` applies to the whole deployment. The
installer asks for it, sets it as Firefly's default currency via the API, and
the bot substitutes it into the `{currency}` placeholder in
[bot/system_prompt.txt](../bot/system_prompt.txt) and
[bot/edit_prompt.txt](../bot/edit_prompt.txt) at startup. Display symbols come
from the map in [bot/currency.py](../bot/currency.py); unknown codes render as
`CODE ` (e.g. `CHF 1,250`).

Amounts must be typed as digits. Spelled-out quantity words — "two hundred",
"1.5 lakh", "crore" — have never been parsed and are not supported.

## Finance Prompt

[bot/system_prompt.txt](../bot/system_prompt.txt) controls transaction extraction.
Update:

- categories
- tag rules
- account examples
- few-shot examples for your spending language (the defaults use Indian
  merchants like Swiggy and Ola; they teach the message shape and work
  anywhere, but you can localize them)

Keep the `{currency}` placeholder — the bot fills it from `CURRENCY` at startup.

The bot expects exactly one tag, usually `firm` or `personal`. If you change this
schema, update report filtering in [bot/reports.py](../bot/reports.py).

## Todo Prompt

[bot/todo_prompt.txt](../bot/todo_prompt.txt) controls reminder extraction.
The default projects are `Work` and `Personal`; if you change these in Vikunja,
update `PROJECT_ALIASES` in [bot/vikunja_client.py](../bot/vikunja_client.py).

## Date Prompt

[bot/date_prompt.txt](../bot/date_prompt.txt) parses defer dates for existing tasks.

## Edit Prompt

[bot/edit_prompt.txt](../bot/edit_prompt.txt) applies conversational corrections to
pending financial transactions.
