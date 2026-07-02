"""Compass — personal finance + todo Telegram bot."""

import asyncio
import json
import logging
import os
import random
import re
import uuid
import time
import requests
import pytz
from datetime import datetime, date, timedelta, time as dtime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler,
    CommandHandler, ContextTypes, filters,
)

from accounts import resolve_account, ACCOUNTS
from firefly_client import FireflyClient, FireflyError
from vikunja_client import VikunjaClient, VikunjaError
from attachment import AttachmentHandler
import reports

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("compass")

def fmt_time(iso_str):
    """Returns HH:MM from Firefly ISO string."""
    try:
        # Firefly dates include the timezone offset which fromisoformat handles
        return datetime.fromisoformat(iso_str).strftime("%H:%M")
    except Exception:
        return ""

# ---------- Config ----------

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
BOT_TIMEZONE = pytz.timezone(os.environ.get("TZ", "Asia/Kolkata"))
ALLOWED_USERS = {
    int(x.strip())
    for x in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",")
    if x.strip()
}
ALLOWED_CHATS = {
    int(x.strip())
    for x in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
    if x.strip()
}
DIGEST_RECIPIENTS = sorted(ALLOWED_USERS)
OLLAMA_URL = os.environ["OLLAMA_URL"]
OLLAMA_MODEL = os.environ["OLLAMA_MODEL"]
OLLAMA_WARMUP_TIMEOUT = int(os.environ.get("OLLAMA_WARMUP_TIMEOUT", "30"))

DIGEST_HOUR = 11
DIGEST_MINUTE = 0

HERE = os.path.dirname(__file__)
def _load(name):
    with open(os.path.join(HERE, name)) as f:
        return f.read()
SYSTEM_PROMPT = _load("system_prompt.txt")
EDIT_PROMPT = _load("edit_prompt.txt")
TODO_PROMPT = _load("todo_prompt.txt")
DATE_PROMPT = _load("date_prompt.txt")

firefly = FireflyClient()
vikunja = VikunjaClient()
attachment_handler = AttachmentHandler()

# Pending state — for both transactions and todos
PENDING: dict[str, dict] = {}
ACTIVE_BY_USER: dict[int, str] = {}
TIMEOUT_SECONDS = 180

# When user taps Defer on a task, we need to wait for their date input.
# Map user_id → task_id they're deferring + chat/message of the digest line.
DEFER_PENDING: dict[int, dict] = {}

# ---------- LLM ----------

def _today_str():
    return datetime.now(BOT_TIMEZONE).date().isoformat()

def _ollama_chat(system: str, user: str, timeout: int = 180) -> dict:
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "format": "json",
            "think": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])

def call_llm_extract(message: str) -> dict:
    return _ollama_chat(SYSTEM_PROMPT, message)

def call_llm_edit(original_parsed: dict, correction: str) -> dict:
    msg = (f"Original: {json.dumps(original_parsed)}\n"
           f"Correction: {correction}\nOutput:")
    return _ollama_chat(EDIT_PROMPT, msg)

def call_llm_todo(message: str) -> dict:
    prompt = TODO_PROMPT.replace("{today}", _today_str())
    return _ollama_chat(prompt, message)

def call_llm_date(phrase: str) -> dict:
    prompt = DATE_PROMPT.replace("{today}", _today_str())
    return _ollama_chat(prompt, phrase)

def warm_model(timeout: int = OLLAMA_WARMUP_TIMEOUT):
    log.info("Warming up model + prompt cache (all prompts)...")
    today = _today_str()
    prompts = [
        ("date",     DATE_PROMPT.replace("{today}", today),    "warmup"),
        ("todo",     TODO_PROMPT.replace("{today}", today),    "warmup"),
        ("edit",     EDIT_PROMPT,                              "warmup"),
        ("finance",  SYSTEM_PROMPT,                            "warmup"),
    ]
    for name, system, user in prompts:
        try:
            _ollama_chat(system, user, timeout=timeout)
            log.info(f"  ✓ {name} prompt cached")
        except Exception as e:
            log.warning(f"  ✗ {name} warm-up failed: {e}")

# ---------- LLM output validation ----------

_TXN_TYPES = {"withdrawal", "deposit", "transfer"}

def invalid_txn_reason(parsed) -> str | None:
    """Sanity-check (and normalize in place) an LLM transaction parse.
    Returns a reason string if the parse is unusable, else None."""
    if not isinstance(parsed, dict):
        return "model did not return an object"
    if parsed.get("type") not in _TXN_TYPES:
        return f"unsupported transaction type: {parsed.get('type')!r}"
    try:
        amount = float(parsed.get("amount"))
    except (TypeError, ValueError):
        return "missing or invalid amount"
    if amount <= 0:
        return "amount must be positive"
    parsed["amount"] = amount
    if not isinstance(parsed.get("tags"), list):
        parsed["tags"] = []
    if not isinstance(parsed.get("description"), str) or not parsed["description"].strip():
        parsed["description"] = "?"
    return None

def invalid_todo_reason(parsed) -> str | None:
    """Sanity-check (and normalize in place) an LLM todo parse."""
    if not isinstance(parsed, dict):
        return "model did not return an object"
    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        return "missing task title"
    project = parsed.get("project")
    if not isinstance(project, str) or not project.strip():
        return "missing project"
    if not isinstance(parsed.get("recurrence"), dict):
        parsed["recurrence"] = None
    return None

# ---------- Card formatting ----------

def md(value) -> str:
    return escape_markdown(str(value), version=1)

def fmt_date(d) -> str:
    """Format a date or ISO string as DD-MM-YYYY."""
    if not d:
        return "?"
    if isinstance(d, str):
        # Strip time portion if present
        d = d.split("T")[0]
        try:
            d = date.fromisoformat(d)
        except ValueError:
            return d  # give up, show as-is
    return d.strftime("%d-%m-%Y")

DISPLAY_TYPE = {
    "withdrawal": ("Expense", "💸"),
    "deposit":    ("Income",  "💰"),
    "transfer":   ("Transfer","🔀"),
}

PRIORITY_LABEL = {
    0: "—", 1: "low", 2: "medium", 3: "high", 4: "urgent",
}

def format_txn_card(parsed: dict, src_canonical: str | None,
                    dst_canonical: str | None) -> str:
    label, emoji = DISPLAY_TYPE.get(parsed["type"], ("?", "❓"))
    lines = [f"{emoji} *{label}*  ₹{parsed['amount']:,}", ""]
    if parsed["type"] == "withdrawal":
        lines.append(f"From: `{md(src_canonical or '(pick one)')}`")
        lines.append(f"To:   {md(parsed.get('destination_raw', '?'))}")
    elif parsed["type"] == "deposit":
        lines.append(f"From: {md((parsed.get('source_raw') or '?').title())}")
        lines.append(f"To:   `{md(dst_canonical or '(pick one)')}`")
    elif parsed["type"] == "transfer":
        lines.append(f"From: `{md(src_canonical or '(pick one)')}`")
        lines.append(f"To:   `{md(dst_canonical or '(pick one)')}`")
    lines.extend([
        "",
        f"📁 {md(parsed.get('category', '?'))}",
        f"🏷  {md(', '.join(parsed.get('tags', [])) or '?')}",
        f"📝 {md(parsed.get('description', '?'))}",
    ])
    if parsed.get("confidence") == "low":
        lines.append("\n⚠️ _Low confidence — double-check_")
    return "\n".join(lines)

def format_todo_card(parsed: dict) -> str:
    lines = [f"📝 *Todo:* {md(parsed.get('title', '?'))}", ""]
    lines.append(f"📁 Project: `{md(parsed.get('project', '?'))}`")
    if parsed.get("due_date"):
        lines.append(f"📅 Due: {fmt_date(parsed['due_date'])}")
    else:
        lines.append("📅 Due: _no date_")
    try:
        pri = int(parsed.get("priority", 0) or 0)
    except (TypeError, ValueError):
        pri = 0
    if pri > 0:
        lines.append(f"⚡ Priority: {PRIORITY_LABEL.get(pri, pri)}")
    rec = parsed.get("recurrence")
    if rec:
        days = rec.get("interval_days")
        mode = rec.get("mode")
        lines.append(f"🔁 Repeats every {md(days)} days ({md(mode)})")
    if parsed.get("confidence") == "low":
        lines.append("\n⚠️ _Low confidence — double-check_")
    return "\n".join(lines)

def kb_confirm(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{pending_id}"),
                InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{pending_id}"),
                InlineKeyboardButton(text="🗑 Cancel", callback_data=f"cancel:{pending_id}"),
            ],
            [
                InlineKeyboardButton(text="📎 Attach File", callback_data=f"attach:{pending_id}"),
            ],
        ]
    )

def kb_todo_confirm(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{pending_id}"),
        InlineKeyboardButton(text="🗑 Cancel", callback_data=f"cancel:{pending_id}"),
    ]])

# Stable, sorted account list so picker buttons can reference accounts by
# index — full names would overflow Telegram's 64-byte callback_data limit.
ACCOUNT_CHOICES = sorted(ACCOUNTS)

def kb_account_picker(pending_id: str, slot: str) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(ACCOUNT_CHOICES), 2):
        row = [InlineKeyboardButton(name, callback_data=f"pick{slot}:{pending_id}:{idx}")
               for idx, name in enumerate(ACCOUNT_CHOICES[i:i+2], start=i)]
        rows.append(row)
    rows.append([InlineKeyboardButton("🗑 Cancel", callback_data=f"cancel:{pending_id}")])
    return InlineKeyboardMarkup(rows)

def kb_task_actions(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Done", callback_data=f"taskdone:{task_id}"),
        InlineKeyboardButton("⏰ Defer", callback_data=f"taskdefer:{task_id}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"taskdel:{task_id}"),
    ]])

def kb_attachment_retry(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔁 Retry files", callback_data=f"retryattach:{pending_id}"),
        InlineKeyboardButton("🗑 Discard files", callback_data=f"attachdiscard:{pending_id}"),
    ]])

def attachment_note(ok: int, fail: int) -> str:
    if ok == 0 and fail == 0:
        return ""
    note = f"\n📎 {ok} file{'s' if ok != 1 else ''} attached"
    if fail:
        note += f" ({fail} failed)"
    return note

# ---------- Pending lifecycle ----------

def touch(pending_id: str):
    if pending_id in PENDING:
        PENDING[pending_id]["last_activity"] = time.time()

def discard(pending_id: str):
    p = PENDING.pop(pending_id, None)
    # Only unmap the user if this pending is still their active one —
    # the timeout watcher may discard an old card after the user has
    # already started a new transaction.
    if p and ACTIVE_BY_USER.get(p["user_id"]) == pending_id:
        ACTIVE_BY_USER.pop(p["user_id"], None)

async def timeout_watcher(application: Application):
    while True:
        await asyncio.sleep(30)
        now = time.time()
        expired = [pid for pid, p in PENDING.items()
                   if now - p["last_activity"] > TIMEOUT_SECONDS]
        for pid in expired:
            p = PENDING.get(pid)
            if not p:
                continue
            user_data = application.user_data.get(p["user_id"])
            if user_data is not None:
                attachment_handler.clear_pending_files(user_data, pid)
            try:
                await application.bot.edit_message_text(
                    chat_id=p["card_chat_id"],
                    message_id=p["card_message_id"],
                    text="⌛ Entry timed out.",
                )
            except Exception as e:
                log.warning(f"Couldn't edit expired card {pid}: {e}")
            discard(pid)

# ---------- Card render helpers ----------

async def send_or_update_txn_card(update_or_query, context: ContextTypes.DEFAULT_TYPE, pending_id: str, *, edit: bool = False):
    p = PENDING[pending_id]
    parsed = p["parsed"]

    # Check for files queued for this specific transaction
    pending_files = attachment_handler.get_pending_files(context.user_data, pending_id)
    file_note = ""
    if pending_files:
        count = len(pending_files)
        file_note = f"\n📎 *{count} file{'s' if count > 1 else ''} queued*"

    card = format_txn_card(parsed, p["src_canonical"], p["dst_canonical"])

    if p["state"] == "awaiting_source_pick":
        kb = kb_account_picker(pending_id, "src")
        text = f"{card}{file_note}\n\n_Which source account?_"
    elif p["state"] == "awaiting_dest_pick":
        kb = kb_account_picker(pending_id, "dst")
        text = f"{card}{file_note}\n\n_Which destination account?_"
    else:
        kb = kb_confirm(pending_id)
        text = f"{card}{file_note}"

    if edit:
        await update_or_query.callback_query.message.edit_text(
            text, parse_mode="Markdown", reply_markup=kb,
        )
    else:
        msg = await update_or_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=kb,
        )
        p["card_chat_id"] = msg.chat_id
        p["card_message_id"] = msg.message_id

# ---------- Authorization & helpers ----------

def is_authorized(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if not user or user.id not in ALLOWED_USERS or not chat:
        return False
    if chat.type == "private":
        return True
    return chat.id in ALLOWED_CHATS

HAS_DIGIT = re.compile(r"\d")

def looks_like_new_input(text: str) -> bool:
    return bool(HAS_DIGIT.search(text)) or text.lower().startswith("remind")

# ---------- /start, /help ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    await update.message.reply_text(
        "Hi! Send me an expense, transfer, income, or a reminder starting with 'remind'.",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    text = """🧭 *Compass commands*

*Logging:*
Just type a transaction or a reminder. Examples:
- `swiggy 250 lunch checking personal`
- `remind me to file GST by friday`

*Reports:*
/balances — bank + card balances
/categories — list of all categories
/today — today's transactions
/yesterday — yesterday's transactions
/thisweek — this week's transactions
/thismonth — this month's transactions

Add `firm` or `personal` after a period:
/today firm
/thismonth personal

*Tasks:*
/tasks — open tasks (overdue + due this week)
/tasks personal — only personal
/tasks work — only work

/help — this message"""
    await update.message.reply_text(text, parse_mode="Markdown")

# ---------- Reports commands ----------

async def cmd_balances(update, ctx):
    if not is_authorized(update): return
    try:
        text = await asyncio.to_thread(reports.balances, firefly)
    except Exception as e:
        log.exception("balances failed"); text = f"❌ {md(e)}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_categories(update, ctx):
    if not is_authorized(update): return
    try:
        text = await asyncio.to_thread(reports.categories, firefly)
    except Exception as e:
        log.exception("categories failed"); text = f"❌ {md(e)}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def _period_command(update, ctx, period):
    if not is_authorized(update): return
    tag = None
    if ctx.args:
        arg = ctx.args[0].lower()
        if arg in ("firm", "personal"):
            tag = arg
        else:
            await update.message.reply_text(f"Unknown filter `{md(arg)}`.", parse_mode="Markdown")
            return
    try:
        text = await asyncio.to_thread(reports.transactions, firefly, period, tag_filter=tag)
        if text is None: text = "❌ Report returned None"
    except Exception as e:
        log.exception(f"{period} failed"); text = f"❌ {md(e)}"
    if len(text) > 4000:
        text = text[:3950] + "\n\n_…(truncated)_"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_today(u, c):     await _period_command(u, c, "today")
async def cmd_yesterday(u, c): await _period_command(u, c, "yesterday")
async def cmd_thisweek(u, c):  await _period_command(u, c, "thisweek")
async def cmd_thismonth(u, c): await _period_command(u, c, "thismonth")

# ---------- Tasks ----------

def format_task_line(task: dict) -> str:
    title = md(task.get("title", "?"))
    due = task.get("due_date")
    try:
        pri = int(task.get("priority", 0) or 0)
    except (TypeError, ValueError):
        pri = 0
    project_id = task.get("project_id")
    overdue = ""
    if due and due != "0001-01-01T00:00:00Z":
        try:
            d = datetime.fromisoformat(due.replace("Z", "+00:00")).date()
            today = datetime.now(BOT_TIMEZONE).date()
            if d < today:
                overdue = "⚠️ "
            due_str = d.strftime("%d %b")
        except Exception:
            due_str = "?"
    else:
        due_str = "no date"
    pri_str = f" [{PRIORITY_LABEL.get(pri, pri)}]" if pri > 0 else ""

    # Project name lookup
    project_name = "?"
    if project_id and vikunja._project_cache:
        for name, pid in vikunja._project_cache.items():
            if pid == project_id:
                project_name = name
                break
    project_emoji = "💼" if project_name == "Work" else "🏠" if project_name == "Personal" else "📁"

    return f"{overdue}*{title}*{pri_str}\n  📅 {due_str}  {project_emoji} {md(project_name)}"

async def _send_tasks(chat_id: int, context: ContextTypes.DEFAULT_TYPE,
                      project_filter: str = None, header: str = None):
    try:
        all_tasks = await asyncio.to_thread(vikunja.list_tasks, project_filter=project_filter)
    except VikunjaError as e:
        await context.bot.send_message(chat_id, f"❌ Vikunja: {md(e)}", parse_mode="Markdown")
        return

    today = datetime.now(BOT_TIMEZONE).date()
    week_end = today + timedelta(days=7)

    def task_due(t):
        d = t.get("due_date")
        if not d or d == "0001-01-01T00:00:00Z": return None
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00")).date()
        except Exception:
            return None

    overdue = [t for t in all_tasks if (d := task_due(t)) and d < today]
    today_tasks = [t for t in all_tasks if (d := task_due(t)) and d == today]
    soon = [t for t in all_tasks if (d := task_due(t)) and today < d <= week_end]
    no_date = [t for t in all_tasks if task_due(t) is None]

    if not (overdue or today_tasks or soon or no_date):
        await context.bot.send_message(
            chat_id,
            f"{header or '📋 *Tasks*'}\n\n_No open tasks. Nice._",
            parse_mode="Markdown",
        )
        return

    # Build header summary message
    lines = [header or "📋 *Open tasks*", ""]
    if overdue:    lines.append(f"⚠️ Overdue: {len(overdue)}")
    if today_tasks: lines.append(f"📅 Today: {len(today_tasks)}")
    if soon:       lines.append(f"📆 This week: {len(soon)}")
    if no_date:    lines.append(f"📭 No date: {len(no_date)}")
    await context.bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

    # Send each task as its own message with action buttons
    for group_label, group in [
        ("⚠️ Overdue", overdue),
        ("📅 Today", today_tasks),
        ("📆 This week", soon),
        ("📭 No date", no_date),
    ]:
        for t in group:
            await context.bot.send_message(
                chat_id,
                format_task_line(t),
                parse_mode="Markdown",
                reply_markup=kb_task_actions(t["id"]),
            )

async def cmd_tasks(update, ctx):
    if not is_authorized(update): return
    project = None
    if ctx.args:
        arg = ctx.args[0].lower()
        if arg in ("personal", "work", "office"):
            project = arg
        else:
            await update.message.reply_text(f"Unknown filter `{md(arg)}`.", parse_mode="Markdown")
            return
    await _send_tasks(update.effective_chat.id, ctx, project_filter=project)

# ---------- Search & Edit Commands ----------

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if not context.args:
        await update.message.reply_text("🔍 Usage: `/search keyword`", parse_mode="Markdown")
        return

    query = " ".join(context.args)
    try:
        results = await asyncio.to_thread(firefly.search_transactions, query)
        if not results:
            await update.message.reply_text("🤷 No transactions found matching that keyword.")
            return

        lines = ["🔍 *Search Results:*", ""]
        for item in results:
            attr = item["attributes"]["transactions"][0]
            t_id = item["id"]

            date_part = fmt_date(attr["date"])
            time_part = fmt_time(attr["date"])
            amount_str = f"₹{float(attr['amount']):.2f}"

            desc = attr["description"]
            if len(desc) > 40:
                desc = desc[:40] + "…"

            lines.append(f"#{t_id} | {date_part} {time_part} | *{amount_str}*")
            lines.append(f"📝 {md(desc)}")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.exception("Search failed"); await update.message.reply_text(f"❌ {md(e)}", parse_mode="Markdown")

async def cmd_edit_txn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if not context.args:
        await update.message.reply_text("✏️ Usage: `/edit <ID>`", parse_mode="Markdown")
        return

    txn_id = context.args[0]
    user_id = update.effective_user.id

    try:
        data = await asyncio.to_thread(firefly.get_transaction, txn_id)
        splits = data["attributes"]["transactions"]
        if len(splits) != 1:
            # Updating would replace the whole group with a single split,
            # silently deleting the others.
            await update.message.reply_text(
                f"✏️ Transaction #{md(txn_id)} has {len(splits)} splits. "
                "Edit it in the Firefly web UI instead.",
                parse_mode="Markdown",
            )
            return
        attr = splits[0]
        source_name = attr.get("source_name") or ""
        destination_name = attr.get("destination_name") or ""
        parsed_type = attr["type"]

        src_status, src_value = resolve_account(source_name)
        dst_status, dst_value = resolve_account(destination_name)
        src_canonical = src_value if src_status == "match" else source_name
        dst_canonical = None

        if parsed_type in ("transfer", "deposit") and dst_status == "match":
            dst_canonical = dst_value

        # Firefly stores asset-to-liability payments as withdrawals. Convert
        # them back to Compass' transfer intent so edits preserve card payments.
        if parsed_type == "withdrawal" and dst_status == "match":
            try:
                if await asyncio.to_thread(firefly.is_liability, dst_value):
                    parsed_type = "transfer"
                    dst_canonical = dst_value
            except FireflyError as e:
                log.warning(f"Could not classify edit destination account: {e}")

        # Map Firefly data back to our internal 'parsed' format
        parsed = {
            "type": parsed_type,
            "amount": float(attr["amount"]),
            "currency": attr.get("currency_code", "INR"),
            "date": attr.get("date"),
            "source_raw": source_name,
            "destination_raw": destination_name,
            "category": attr["category_name"] or "",
            "tags": attr["tags"] or [],
            "description": attr["description"],
        }

        pending_id = uuid.uuid4().hex[:8]
        PENDING[pending_id] = {
            "kind": "transaction",
            "parsed": parsed,
            "src_canonical": src_canonical,
            "dst_canonical": dst_canonical,
            "state": "awaiting_confirm",
            "user_id": user_id,
            "is_update": True,        # FLAG: Tells the bot to UPDATE instead of CREATE
            "existing_id": txn_id,   # Reference the original ID
            "last_activity": time.time(),
        }
        ACTIVE_BY_USER[user_id] = pending_id

        await update.message.reply_text(f"✏️ *Editing Transaction #{md(txn_id)}*", parse_mode="Markdown")
        await send_or_update_txn_card(update, context, pending_id, edit=False)

    except Exception as e:
        log.exception("Edit fetch failed"); await update.message.reply_text(f"❌ {md(e)}", parse_mode="Markdown")

# ---------- New Attachment Logic ----------

async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepts documents and photos during the attachment collection phase."""
    if not is_authorized(update):
        return

    pid = attachment_handler.awaiting_pid(context.user_data)
    if pid and pid in PENDING:
        # Refresh the activity timer so the session doesn't time out mid-upload
        touch(pid)
        await attachment_handler.handle_incoming_file(update, context)
        return

    if pid:
        # Collection was open for a transaction that no longer exists
        attachment_handler.clear_pending_files(context.user_data, pid)
    await update.message.reply_text(
        "📎 Send a transaction first, then use the Attach File button."
    )

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/done — finalises file collection and restores the Confirm button."""
    if not is_authorized(update):
        return

    pid = attachment_handler.handle_done_command(context.user_data)
    if pid is None:
        await update.message.reply_text("Nothing to finalise right now.")
        return

    if pid in PENDING:
        await update.message.reply_text("📎 Files queued. Final check before logging:")
        await send_or_update_txn_card(update, context, pid, edit=False)
    else:
        attachment_handler.clear_pending_files(context.user_data, pid)
        await update.message.reply_text("📎 That transaction expired. Please send it again.")

async def send_to_digest_recipients(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    **kwargs,
) -> None:
    for user_id in DIGEST_RECIPIENTS:
        try:
            await context.bot.send_message(user_id, text, **kwargs)
        except Exception as e:
            log.warning(f"Could not send scheduled message to {user_id}: {e}")

# ---------- Daily digest ----------

async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    log.info("Sending daily digest...")
    today = datetime.now(BOT_TIMEZONE).date()
    try:
        all_tasks = await asyncio.to_thread(vikunja.list_tasks)
    except VikunjaError as e:
        log.error(f"Digest failed: {e}")
        return

    def task_due(t):
        d = t.get("due_date")
        if not d or d == "0001-01-01T00:00:00Z": return None
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00")).date()
        except Exception:
            return None

    overdue = [t for t in all_tasks if (d := task_due(t)) and d < today]
    today_tasks = [t for t in all_tasks if (d := task_due(t)) and d == today]

    if not overdue and not today_tasks:
        await send_to_digest_recipients(
            context,
            "🌅 *Good morning!*\n\n_No tasks due today. Enjoy._",
            parse_mode="Markdown",
        )
        return

    lines = [f"🌅 *Good morning!* — {today.strftime('%d %b %Y')}", ""]
    if overdue: lines.append(f"⚠️ {len(overdue)} overdue")
    if today_tasks: lines.append(f"📅 {len(today_tasks)} due today")
    await send_to_digest_recipients(context, "\n".join(lines), parse_mode="Markdown")

    for group_label, group in [("⚠️ Overdue", overdue), ("📅 Today", today_tasks)]:
        for t in group:
            await send_to_digest_recipients(
                context,
                format_task_line(t),
                parse_mode="Markdown",
                reply_markup=kb_task_actions(t["id"]),
            )

async def send_ping_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Simple nudge to log transactions or todos."""
    log.info("Sending scheduled reminder ping...")
    messages = [
        "🔔 *Compass check:* Any expenses or new tasks to log?",
        "📉 *Ledger check:* Don't forget to log today's transactions!",
        "📝 *Todo check:* Anything new for the task list?",
    ]
    text = random.choice(messages)

    await send_to_digest_recipients(context, text, parse_mode="Markdown")

# ---------- Message routing ----------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    log.info(f"📥 {text!r}")

    # Guard: if we're attaching files, don't let random text kill the transaction
    awaiting_pid = attachment_handler.awaiting_pid(context.user_data)
    if awaiting_pid:
        if looks_like_new_input(text):
            # The user moved on to a new transaction: drop the old file session
            attachment_handler.clear_pending_files(context.user_data, awaiting_pid)
        else:
            # Otherwise ignore captions/chat to stay in "Attach Mode"
            return

    # Defer flow: user previously tapped Defer on a task...
    # Always route to date handler — date inputs like "13 May" or "in 3 days"
    # contain digits and would incorrectly fail a looks_like_new_input check.
    if user_id in DEFER_PENDING and not text.lower().startswith("/"):
        await handle_defer_input(update, context, text)
        return

    active_pid = ACTIVE_BY_USER.get(user_id)
    active = PENDING.get(active_pid, {}) if active_pid else {}
    active_state = active.get("state")
    active_kind = active.get("kind")

    if active_state == "awaiting_edit_input" and active_kind == "transaction":
        # The user explicitly tapped Edit — treat whatever they typed as a
        # correction, even if it contains digits ("amount is 300").
        await handle_edit_correction(update, context, active_pid, text)
        return
    elif active_state == "awaiting_confirm" and active_kind == "transaction" and not looks_like_new_input(text):
        await handle_edit_correction(update, context, active_pid, text)
        return
    elif active_state == "awaiting_confirm" and active_kind == "todo" and not looks_like_new_input(text):
        await update.message.reply_text("Tap Confirm or Cancel. To change the task, cancel it and send the reminder again.")
        return

    # Stale-pending cleanup
    stale_pid = ACTIVE_BY_USER.get(user_id)
    if stale_pid and stale_pid in PENDING:
        # Check if we are currently in "Attach Mode"
        # Only cancel if the input actually looks like a NEW transaction
        if looks_like_new_input(text):
            await _cancel_pending(stale_pid, context, "_Cancelled (new input sent)_")

    # Route: todo vs transaction
    if text.lower().startswith("remind"):
        await handle_todo_message(update, context, text)
    else:
        await handle_transaction_message(update, context, text)

async def _cancel_pending(pending_id: str, context: ContextTypes.DEFAULT_TYPE, suffix: str):
    p = PENDING.get(pending_id)
    if not p: return
    try:
        if p.get("kind") == "todo":
            text = format_todo_card(p["parsed"]) + f"\n\n🗑 {suffix}"
        else:
            text = format_txn_card(p["parsed"], p["src_canonical"], p["dst_canonical"]) + f"\n\n🗑 {suffix}"
        await context.bot.edit_message_text(
            chat_id=p["card_chat_id"], message_id=p["card_message_id"],
            text=text, parse_mode="Markdown",
        )
    except Exception as e:
        log.warning(f"Couldn't cancel {pending_id}: {e}")
    discard(pending_id)

# ---------- Transaction flow ----------

async def handle_transaction_message(update, context, text: str):
    user_id = update.effective_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        parsed = await asyncio.to_thread(call_llm_extract, text)
    except Exception as e:
        log.exception("LLM extract failed"); await update.message.reply_text(f"❌ {md(e)}", parse_mode="Markdown"); return

    log.info(f"🧠 {parsed}")

    if parsed.get("type") == "unknown":
        await update.message.reply_text("🤔 Couldn't parse. Try starting with `remind` for a todo.")
        return

    reason = invalid_txn_reason(parsed)
    if reason:
        log.warning(f"Unusable transaction parse ({reason}): {parsed!r}")
        await update.message.reply_text("🤔 Couldn't parse that into a transaction. Try rephrasing.")
        return

    src_status, src_value = resolve_account(parsed.get("source_raw") or "")
    dst_canonical = None
    dst_status = "match"
    if parsed["type"] in ("transfer", "deposit"):
        dst_status, dst_value = resolve_account(parsed.get("destination_raw") or "")
        if dst_status == "match": dst_canonical = dst_value

    if src_status != "match" and parsed["type"] != "deposit":
        state = "awaiting_source_pick"
    elif parsed["type"] in ("transfer", "deposit") and dst_status != "match":
        state = "awaiting_dest_pick"
    else:
        state = "awaiting_confirm"

    pending_id = uuid.uuid4().hex[:8]
    PENDING[pending_id] = {
        "kind": "transaction",
        "parsed": parsed,
        "src_canonical": src_value if src_status == "match" else None,
        "dst_canonical": dst_canonical,
        "state": state,
        "user_id": user_id,
        "card_chat_id": None, "card_message_id": None,
        "last_activity": time.time(),
    }
    ACTIVE_BY_USER[user_id] = pending_id
    await send_or_update_txn_card(update, context, pending_id, edit=False)

async def handle_edit_correction(update, context, pending_id: str, correction: str):
    p = PENDING[pending_id]
    touch(pending_id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        new_parsed = await asyncio.to_thread(call_llm_edit, p["parsed"], correction)
    except Exception as e:
        log.exception("edit failed"); await update.message.reply_text(f"❌ {md(e)}", parse_mode="Markdown"); return

    log.info(f"✏️ {new_parsed}")

    reason = invalid_txn_reason(new_parsed)
    if reason:
        log.warning(f"Unusable correction parse ({reason}): {new_parsed!r}")
        await update.message.reply_text("🤔 Couldn't apply that correction. Try rephrasing.")
        return

    p["parsed"] = new_parsed
    src_status, src_value = resolve_account(new_parsed.get("source_raw") or "")
    p["src_canonical"] = src_value if src_status == "match" else None
    if new_parsed["type"] in ("transfer", "deposit"):
        ds, dv = resolve_account(new_parsed.get("destination_raw") or "")
        p["dst_canonical"] = dv if ds == "match" else None
    else:
        p["dst_canonical"] = None

    if p["src_canonical"] is None and new_parsed["type"] != "deposit":
        p["state"] = "awaiting_source_pick"
    elif new_parsed["type"] in ("transfer", "deposit") and p["dst_canonical"] is None:
        p["state"] = "awaiting_dest_pick"
    else:
        p["state"] = "awaiting_confirm"

    try:
        text = format_txn_card(p["parsed"], p["src_canonical"], p["dst_canonical"])
        if p["state"] == "awaiting_confirm":
            text += "\n\n_What else to change? Or tap Confirm._"
            kb = kb_confirm(pending_id)
        elif p["state"] == "awaiting_source_pick":
            text += "\n\n_Which source account?_"
            kb = kb_account_picker(pending_id, "src")
        else:
            text += "\n\n_Which destination account?_"
            kb = kb_account_picker(pending_id, "dst")
        await context.bot.edit_message_text(
            chat_id=p["card_chat_id"], message_id=p["card_message_id"],
            text=text, parse_mode="Markdown", reply_markup=kb,
        )
    except Exception as e:
        log.warning(f"Couldn't update card: {e}")

# ---------- Todo flow ----------

async def handle_todo_message(update, context, text: str):
    user_id = update.effective_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        parsed = await asyncio.to_thread(call_llm_todo, text)
    except Exception as e:
        log.exception("todo LLM failed"); await update.message.reply_text(f"❌ {md(e)}", parse_mode="Markdown"); return

    log.info(f"🧠 todo: {parsed}")

    reason = invalid_todo_reason(parsed)
    if reason:
        log.warning(f"Unusable todo parse ({reason}): {parsed!r}")
        await update.message.reply_text("🤔 Couldn't parse that reminder. Try rephrasing.")
        return

    pending_id = uuid.uuid4().hex[:8]
    PENDING[pending_id] = {
        "kind": "todo",
        "parsed": parsed,
        "state": "awaiting_confirm",
        "user_id": user_id,
        "card_chat_id": None, "card_message_id": None,
        "last_activity": time.time(),
    }
    ACTIVE_BY_USER[user_id] = pending_id

    msg = await update.message.reply_text(
        format_todo_card(parsed), parse_mode="Markdown",
        reply_markup=kb_todo_confirm(pending_id),
    )
    PENDING[pending_id]["card_chat_id"] = msg.chat_id
    PENDING[pending_id]["card_message_id"] = msg.message_id

# ---------- Defer flow ----------

async def handle_defer_input(update, context, phrase: str):
    user_id = update.effective_user.id
    info = DEFER_PENDING.get(user_id)
    if not info:
        return
    task_id = info["task_id"]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        result = await asyncio.to_thread(call_llm_date, phrase)
        new_date = result["date"]
    except Exception as e:
        log.exception("date parse failed")
        await update.message.reply_text(f"❌ Couldn't parse date: {md(e)}", parse_mode="Markdown")
        return

    try:
        await asyncio.to_thread(vikunja.update_task, task_id, due_date=new_date)
    except VikunjaError as e:
        await update.message.reply_text(f"❌ Vikunja: {md(e)}", parse_mode="Markdown")
        return

    DEFER_PENDING.pop(user_id, None)

    # Update the original task message to reflect the new date
    try:
        await context.bot.edit_message_text(
            chat_id=info["chat_id"], message_id=info["message_id"],
            text=info["original_text"] + f"\n\n⏰ Deferred to {fmt_date(new_date)}",
        )
    except Exception as e:
        log.warning(f"Couldn't update deferred task message: {e}")

    await update.message.reply_text(f"⏰ Deferred to {fmt_date(new_date)}.")

async def upload_pending_attachments(
    context: ContextTypes.DEFAULT_TYPE,
    pending_id: str,
    journal_id: str,
) -> tuple[int, int]:
    pending_files = attachment_handler.get_pending_files(context.user_data, pending_id)
    if not pending_files:
        return 0, 0

    ok, fail, failed_paths = await attachment_handler.attach_to_transaction(
        journal_id=str(journal_id),
        local_paths=pending_files,
    )
    # Keep only the failed files queued so a retry can't re-upload successes
    attachment_handler.set_pending_files(context.user_data, pending_id, failed_paths)
    return ok, fail

# ---------- Callback handler (covers everything tappable) ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Always answer so the client stops showing a loading spinner,
    # even for unauthorized taps.
    await query.answer()
    if not is_authorized(update):
        return
    data = query.data
    parts = data.split(":", 2)
    action = parts[0]
    pending_id = parts[1] if len(parts) > 1 else None
    extra = parts[2] if len(parts) > 2 else None

    # "Done adding files" button — the collecting session knows its own pending ID
    if action == "attach_done":
        pid = attachment_handler.handle_done_command(context.user_data)
        if pid and pid in PENDING:
            p = PENDING[pid]
            p["state"] = "awaiting_confirm"
            touch(pid)
            await send_or_update_txn_card(update, context, pid, edit=True)
        return

    # Task action callbacks (Done/Defer/Delete on existing Vikunja tasks)
    if action in ("taskdone", "taskdefer", "taskdel"):
        try:
            task_id = int(parts[1])
        except (IndexError, ValueError):
            return

        original_text = query.message.text or ""

        if action == "taskdone":
            try:
                await asyncio.to_thread(vikunja.mark_done, task_id)
                await query.edit_message_text(f"{original_text}\n\n✅ Done")
            except VikunjaError as e:
                await query.edit_message_text(f"{original_text}\n\n❌ {e}")
            return

        if action == "taskdel":
            try:
                await asyncio.to_thread(vikunja.delete_task, task_id)
                await query.edit_message_text(f"{original_text}\n\n🗑 Deleted")
            except VikunjaError as e:
                await query.edit_message_text(f"{original_text}\n\n❌ {e}")
            return

        if action == "taskdefer":
            DEFER_PENDING[update.effective_user.id] = {
                "task_id": task_id,
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
                "original_text": original_text,
            }
            await context.bot.send_message(
                query.message.chat_id,
                "⏰ Defer until when? (e.g. tomorrow, next monday, 15 may)",
            )
            return

    # Pending (transaction or todo) callbacks
    p = PENDING.get(pending_id)
    if p is None:
        await query.edit_message_text("⌛ Expired or already handled.")
        return

    # Refresh the timer and handle the specific button
    touch(pending_id)
    if p.get("kind") == "todo":
        original_card = format_todo_card(p["parsed"])
    else:
        original_card = format_txn_card(p["parsed"], p["src_canonical"], p["dst_canonical"])

    if action == "retryattach":
        if p.get("state") != "awaiting_attachment_retry" or not p.get("journal_id"):
            await query.edit_message_text(
                f"{original_card}\n\n📎 _No failed attachment upload to retry._",
                parse_mode="Markdown",
            )
            return

        ok, fail = await upload_pending_attachments(context, pending_id, str(p["journal_id"]))
        p["attachment_success_count"] = p.get("attachment_success_count", 0) + ok
        note = attachment_note(p["attachment_success_count"], fail)
        status_label = p.get("status_label", "Transaction saved")

        if fail:
            touch(pending_id)
            await query.edit_message_text(
                f"{original_card}\n\n✅ _{status_label}_{note}\n\n"
                "_Failed files are still saved. Retry upload or discard them._",
                parse_mode="Markdown",
                reply_markup=kb_attachment_retry(pending_id),
            )
            return

        discard(pending_id)
        await query.edit_message_text(
            f"{original_card}\n\n✅ _{status_label}_{note}",
            parse_mode="Markdown",
        )
        return

    if action == "attachdiscard":
        status_label = p.get("status_label", "Transaction saved")
        attachment_handler.clear_pending_files(context.user_data, pending_id)
        discard(pending_id)
        await query.edit_message_text(
            f"{original_card}\n\n✅ _{status_label}_\n\n🗑 _Discarded failed attachment files._",
            parse_mode="Markdown",
        )
        return

    if action == "attach":
        if p.get("kind") != "transaction":
            await query.edit_message_text(f"{original_card}\n\n📎 _Attachments are only for transactions._", parse_mode="Markdown")
            return
        await attachment_handler.prompt_for_files(update, context, pending_id)
        return

    if action == "cancel":
        attachment_handler.clear_pending_files(context.user_data, pending_id)
        discard(pending_id)
        await query.edit_message_text(f"{original_card}\n\n🗑 _Cancelled_", parse_mode="Markdown")
        return

    if action == "edit":
        if p.get("kind") != "transaction":
            await query.edit_message_text(
                f"{original_card}\n\n✏️ _Cancel and send the reminder again to change it._",
                parse_mode="Markdown",
                reply_markup=kb_todo_confirm(pending_id),
            )
            return
        p["state"] = "awaiting_edit_input"
        await query.edit_message_text(f"{original_card}\n\n✏️ _What should I change?_", parse_mode="Markdown")
        return

    if action in ("picksrc", "pickdst"):
        try:
            choice = ACCOUNT_CHOICES[int(extra)]
        except (TypeError, ValueError, IndexError):
            log.warning(f"Invalid account pick payload: {data!r}")
            return
        if action == "picksrc":
            p["src_canonical"] = choice
            if p["parsed"]["type"] in ("transfer", "deposit") and p["dst_canonical"] is None:
                p["state"] = "awaiting_dest_pick"
            else:
                p["state"] = "awaiting_confirm"
        else:
            p["dst_canonical"] = choice
            p["state"] = "awaiting_confirm"
        await send_or_update_txn_card(update, context, pending_id, edit=True)
        return

    if action == "confirm":
        if p.get("state") == "processing":
            await query.answer("Already processing.")
            return

        p["state"] = "processing"
        touch(pending_id)

        try:
            await query.edit_message_text(f"{original_card}\n\n⏳ _Processing..._", parse_mode="Markdown")
        except Exception as e:
            log.warning(f"Couldn't mark {pending_id} as processing: {e}")

        if p.get("kind") == "todo":
            try:
                result = await asyncio.to_thread(vikunja.create_task, p["parsed"])
                tid = result.get("id", "?")
                discard(pending_id)
                await query.edit_message_text(f"{original_card}\n\n✅ _Created. Task #{tid}_", parse_mode="Markdown")
            except Exception as e:
                log.exception("Vikunja create failed")
                p["state"] = "awaiting_confirm"
                await query.edit_message_text(
                    f"{original_card}\n\n❌ _Vikunja: {md(e)}_\n\n_Tap Confirm to retry._",
                    parse_mode="Markdown",
                    reply_markup=kb_todo_confirm(pending_id),
                )
        else: # Transaction path
            try:
                now_str = datetime.now(BOT_TIMEZONE).strftime("%H:%M")
                if p.get("is_update"):
                    txn_id = p["existing_id"]
                    update_data = firefly.build_transaction_payload(
                        parsed=p["parsed"],
                        source_canonical=p["src_canonical"],
                        destination_canonical=p["dst_canonical"],
                    )
                    result = await asyncio.to_thread(firefly.update_transaction, txn_id, update_data)
                    journal_id = result["attributes"]["transactions"][0]["transaction_journal_id"]
                    status_label = f"Updated at {now_str}. Transaction #{txn_id}"
                else:
                    result = await asyncio.to_thread(
                        firefly.create_transaction,
                        parsed=p["parsed"],
                        source_canonical=p["src_canonical"],
                        destination_canonical=p["dst_canonical"],
                    )
                    txn_id = result["id"]
                    journal_id = result["attributes"]["transactions"][0].get("transaction_journal_id", txn_id)
                    status_label = f"Logged at {now_str}. Transaction #{txn_id}"

                ok, fail = await upload_pending_attachments(context, pending_id, str(journal_id))
                p["attachment_success_count"] = ok
                attach_note = attachment_note(ok, fail)
                if fail:
                    p["state"] = "awaiting_attachment_retry"
                    p["journal_id"] = str(journal_id)
                    p["status_label"] = status_label
                    touch(pending_id)
                    await query.edit_message_text(
                        f"{original_card}\n\n✅ _{status_label}_{attach_note}\n\n"
                        "_Failed files are still saved. Retry upload or discard them._",
                        parse_mode="Markdown",
                        reply_markup=kb_attachment_retry(pending_id),
                    )
                    return

                discard(pending_id)

                await query.edit_message_text(
                    f"{original_card}\n\n✅ _{status_label}_{attach_note}",
                    parse_mode="Markdown",
                )

            except Exception as e:
                log.exception("Firefly push failed")
                p["state"] = "awaiting_confirm"
                await query.edit_message_text(
                    f"{original_card}\n\n❌ _{md(e)}_\n\n_Tap Confirm to retry._",
                    parse_mode="Markdown",
                    reply_markup=kb_confirm(pending_id),
                )
        return

# ---------- Main ----------

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Last-resort handler so unexpected exceptions are logged and the user
    is told something went wrong instead of getting silence."""
    log.error("Unhandled exception while processing an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "❌ Something went wrong — check the bot logs.",
            )
        except Exception:
            pass

async def post_init(application: Application):
    asyncio.create_task(timeout_watcher(application))
    asyncio.create_task(asyncio.to_thread(warm_model, OLLAMA_WARMUP_TIMEOUT))

def main():
    log.info("Starting Compass bot...")
    app = (Application.builder()
           .token(TELEGRAM_TOKEN)
           .post_init(post_init)
           .read_timeout(15)
           .write_timeout(15)
           .build())

    # Schedule daily digest in the configured bot timezone.
    app.job_queue.run_daily(
        daily_digest,
        time=dtime(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, tzinfo=BOT_TIMEZONE),
        name="daily_digest",
    )

    # Scheduled reminder nudges in the configured bot timezone.
    reminder_times = [
        dtime(hour=12, minute=0, tzinfo=BOT_TIMEZONE),  # 12:00 PM
        dtime(hour=17, minute=0, tzinfo=BOT_TIMEZONE),  # 5:00 PM
        dtime(hour=22, minute=0, tzinfo=BOT_TIMEZONE),  # 10:00 PM
    ]

    for i, t in enumerate(reminder_times):
        app.job_queue.run_daily(
            send_ping_reminder,
            time=t,
            name=f"reminder_ping_{i}"
        )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("edit", cmd_edit_txn))
    app.add_handler(CommandHandler("balances", cmd_balances))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("yesterday", cmd_yesterday))
    app.add_handler(CommandHandler("thisweek", cmd_thisweek))
    app.add_handler(CommandHandler("thismonth", cmd_thismonth))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done)) 
    # File handler — must be before the text handler so files are intercepted
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, on_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
