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

## Finance Prompt

[bot/system_prompt.txt](../bot/system_prompt.txt) controls transaction extraction.
Update:

- categories
- tag rules
- account examples
- few-shot examples for your spending language

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
