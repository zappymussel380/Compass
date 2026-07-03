import pytest

import bot


# ---------- LLM output validation ----------

def test_valid_txn_parse_normalized():
    parsed = {"type": "withdrawal", "amount": "250", "tags": None, "description": ""}
    assert bot.invalid_txn_reason(parsed) is None
    assert parsed["amount"] == 250.0
    assert parsed["tags"] == []
    assert parsed["description"] == "?"


@pytest.mark.parametrize("parsed", [
    "not a dict",
    {"type": "magic", "amount": 10},
    {"type": "withdrawal"},                       # no amount
    {"type": "withdrawal", "amount": "lots"},     # non-numeric
    {"type": "withdrawal", "amount": -5},         # negative
    {"type": "withdrawal", "amount": 0},          # zero
])
def test_bad_txn_parses_rejected(parsed):
    assert bot.invalid_txn_reason(parsed) is not None


def test_valid_todo_parse():
    parsed = {"title": "File GST", "project": "Work", "recurrence": None}
    assert bot.invalid_todo_reason(parsed) is None


def test_todo_recurrence_of_wrong_type_dropped():
    parsed = {"title": "X", "project": "Personal", "recurrence": "weekly"}
    assert bot.invalid_todo_reason(parsed) is None
    assert parsed["recurrence"] is None


@pytest.mark.parametrize("parsed", [
    "not a dict",
    {"project": "Work"},                 # no title
    {"title": "  ", "project": "Work"},  # blank title
    {"title": "X"},                      # no project
])
def test_bad_todo_parses_rejected(parsed):
    assert bot.invalid_todo_reason(parsed) is not None


# ---------- routing heuristic ----------

def test_looks_like_new_input():
    assert bot.looks_like_new_input("swiggy 250 lunch checking")
    assert bot.looks_like_new_input("remind me to file gst")
    assert not bot.looks_like_new_input("actually make it firm")


# ---------- pending lifecycle ----------

def test_discard_only_unmaps_own_user_entry():
    """Regression: the timeout watcher must not unmap a user's newer pending."""
    bot.PENDING.clear()
    bot.ACTIVE_BY_USER.clear()
    bot.PENDING["old1"] = {"user_id": 42, "last_activity": 0}
    bot.PENDING["new1"] = {"user_id": 42, "last_activity": 0}
    bot.ACTIVE_BY_USER[42] = "new1"

    bot.discard("old1")
    assert bot.ACTIVE_BY_USER[42] == "new1"

    bot.discard("new1")
    assert 42 not in bot.ACTIVE_BY_USER


# ---------- formatting ----------

def test_fmt_date_variants():
    assert bot.fmt_date("2026-07-02") == "02-07-2026"
    assert bot.fmt_date("2026-07-02T10:00:00+05:30") == "02-07-2026"
    assert bot.fmt_date(None) == "?"
    assert bot.fmt_date("gibberish") == "gibberish"


def test_format_txn_card_withdrawal():
    parsed = {
        "type": "withdrawal", "amount": 250.0,
        "destination_raw": "Swiggy", "category": "Food and Drinks",
        "tags": ["personal"], "description": "Swiggy lunch",
    }
    card = bot.format_txn_card(parsed, "Primary Checking", None)
    assert "Expense" in card
    assert "250" in card
    assert "Primary Checking" in card
    assert "Swiggy" in card


def test_format_todo_card_with_recurrence():
    parsed = {
        "title": "Pay card bill", "project": "Personal",
        "due_date": "2026-07-05", "priority": 2,
        "recurrence": {"interval_days": 30, "mode": "monthly"},
    }
    card = bot.format_todo_card(parsed)
    assert "Pay card bill" in card
    assert "05-07-2026" in card
    assert "medium" in card
    assert "Repeats every 30 days" in card


def test_attachment_note():
    assert bot.attachment_note(0, 0) == ""
    assert "1 file attached" in bot.attachment_note(1, 0)
    assert "2 files attached (1 failed)" in bot.attachment_note(2, 1)


# ---------- account picker callback data ----------

PICKER_CHOICES = sorted([
    "A Very Long Bank Account Name That Tests Limits",
    "Cash", "Main Checking", "Rewards Card", "Savings",
])


def test_picker_callback_data_within_telegram_limit():
    kb = bot.kb_account_picker("abcdef12", "src", PICKER_CHOICES)
    for row in kb.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64


def test_picker_indices_map_back_to_account_names():
    kb = bot.kb_account_picker("abcdef12", "src", PICKER_CHOICES)
    buttons = [b for row in kb.inline_keyboard for b in row
               if b.callback_data.startswith("picksrc:")]
    assert buttons
    for button in buttons:
        idx = int(button.callback_data.split(":", 2)[2])
        assert PICKER_CHOICES[idx] == button.text


# ---------- schedule parsing ----------

def test_parse_hhmm():
    t = bot._parse_hhmm("09:30")
    assert (t.hour, t.minute) == (9, 30)
    assert t.tzinfo is not None


@pytest.mark.parametrize("value", ["", "25:00x", "noon", "9"])
def test_parse_hhmm_rejects_garbage(value):
    with pytest.raises(ValueError):
        bot._parse_hhmm(value)
