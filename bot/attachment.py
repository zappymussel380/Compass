"""
attachment.py
-------------
Handles bill/proof file attachments on Compass transactions.

Flow:
  1. After a transaction is staged (pending confirm), the user taps 📎 Attach File
  2. Bot prompts for files - user sends PDFs / images one by one
  3. User sends /done (or taps Done button) to finalise
  4. On confirm, files are:
     a. Saved temporarily under ATTACHMENTS_DIR/pending/<pending_id>/
     b. Registered and uploaded to Firefly III via the Attachments API
     c. Deleted locally after upload

Every queued file is tied to the pending transaction it was collected for, so
receipts can never be uploaded to a different transaction's journal.

Supported formats: PDF, JPG, JPEG, PNG, WEBP
Max file size: 20MB (Telegram Bot API limit for documents)
"""

import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import MutableMapping

import httpx
from telegram import Update, Message
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# -- Config --------------------------------------------------------
ATTACHMENTS_DIR = Path(os.getenv("ATTACHMENTS_DIR", "./attachments"))
FIREFLY_BASE_URL = os.getenv("FIREFLY_URL", "http://firefly:8080")
FIREFLY_TOKEN = os.getenv("FIREFLY_TOKEN", "")

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE_MB = 20


# -- Session state keys (stored in context.user_data) ---------------
ATTACHMENT_SESSIONS_KEY = "attachment_sessions"  # dict: pending_id -> [local paths]
AWAITING_ATTACHMENT_KEY = "awaiting_attachment"  # pending_id currently collecting


class AttachmentHandler:
    """
    Manages the full lifecycle of transaction attachments.
    Instantiate once and register handlers in your bot setup.
    """

    def __init__(self):
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        self._purge_stale_pending()
        logger.info(f"[Attachments] Storage root: {ATTACHMENTS_DIR}")

    @staticmethod
    def _purge_stale_pending() -> None:
        """Remove files left over from sessions that died with a previous run.
        Pending state lives in memory, so after a restart these files can never
        be retried and would otherwise accumulate forever."""
        pending_root = ATTACHMENTS_DIR / "pending"
        if not pending_root.exists():
            return
        try:
            shutil.rmtree(pending_root)
            logger.info("[Attachments] Purged stale pending files from previous run")
        except OSError as e:
            logger.warning(f"[Attachments] Could not purge stale pending files: {e}")

    # ---------- Session state ----------

    @staticmethod
    def awaiting_pid(user_data: MutableMapping) -> str | None:
        """Pending-transaction ID currently collecting files, if any."""
        return user_data.get(AWAITING_ATTACHMENT_KEY)

    @staticmethod
    def get_pending_files(user_data: MutableMapping, pending_id: str) -> list[str]:
        """Local paths of files queued for this pending transaction."""
        return user_data.get(ATTACHMENT_SESSIONS_KEY, {}).get(pending_id, [])

    @staticmethod
    def set_pending_files(
        user_data: MutableMapping, pending_id: str, paths: list[str]
    ) -> None:
        """Replace the queued file list without touching the files on disk."""
        sessions = user_data.setdefault(ATTACHMENT_SESSIONS_KEY, {})
        if paths:
            sessions[pending_id] = paths
        else:
            sessions.pop(pending_id, None)

    def clear_pending_files(
        self, user_data: MutableMapping, pending_id: str
    ) -> None:
        """Drop the session and delete its files and temp directory."""
        sessions = user_data.get(ATTACHMENT_SESSIONS_KEY, {})
        paths = sessions.pop(pending_id, [])
        if user_data.get(AWAITING_ATTACHMENT_KEY) == pending_id:
            user_data.pop(AWAITING_ATTACHMENT_KEY, None)

        for p in paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError as e:
                logger.error(f"[Attachments] Failed to delete file {p}: {e}")

        try:
            temp_dir = ATTACHMENTS_DIR / "pending" / pending_id
            if temp_dir.exists():
                temp_dir.rmdir()  # only succeeds when empty
        except OSError:
            pass

    def handle_done_command(self, user_data: MutableMapping) -> str | None:
        """Finalise file collection. Returns the pending ID that was collecting,
        or None if no collection was in progress."""
        return user_data.pop(AWAITING_ATTACHMENT_KEY, None)

    # ---------- Telegram handlers ----------

    async def prompt_for_files(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, pending_id: str
    ) -> None:
        """Called when the user taps 📎 Attach File on the confirm keyboard."""
        context.user_data[AWAITING_ATTACHMENT_KEY] = pending_id
        context.user_data.setdefault(ATTACHMENT_SESSIONS_KEY, {}).setdefault(
            pending_id, []
        )

        await update.callback_query.edit_message_text(
            "📎 Send me the bill(s) now - PDF or image files, one at a time.\n"
            "When you're done, send /done or tap the button below.",
            reply_markup=_done_keyboard(),
        )

    async def handle_incoming_file(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """
        Receives a document or photo from the user during the attachment phase.
        Returns True if the file was accepted or rejected with feedback,
        False if no collection is in progress.
        """
        pending_id = self.awaiting_pid(context.user_data)
        if not pending_id:
            return False

        message: Message = update.effective_message
        file_obj = None
        filename = None
        mime_type = None

        # Document (PDF or image sent as file)
        if message.document:
            doc = message.document
            mime_type = doc.mime_type or ""
            if mime_type not in ALLOWED_MIME_TYPES:
                await message.reply_text(
                    f"❌ Unsupported type: `{mime_type}`\n"
                    "Please send PDF, JPG, PNG, or WEBP only.",
                    parse_mode="Markdown",
                )
                return True  # handled, just rejected

            if doc.file_size and doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                await message.reply_text(
                    f"❌ File too large (max {MAX_FILE_SIZE_MB}MB)."
                )
                return True

            file_obj = await doc.get_file()
            ext = Path(doc.file_name or "bill.pdf").suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                ext = _mime_to_ext(mime_type)
            filename = f"bill_{_unique_ts()}{ext}"

        # Photo (compressed image - acceptable for receipts)
        elif message.photo:
            photo = message.photo[-1]  # highest resolution
            file_obj = await photo.get_file()
            filename = f"bill_{_unique_ts()}.jpg"
            mime_type = "image/jpeg"

        else:
            return False  # not a file message - let other handlers deal with it

        # Download to a temp location (txn not yet confirmed, no journal_id yet)
        temp_dir = ATTACHMENTS_DIR / "pending" / pending_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        local_path = temp_dir / filename

        await file_obj.download_to_drive(local_path)
        logger.info(f"[Attachments] Saved pending file: {local_path}")

        sessions = context.user_data.setdefault(ATTACHMENT_SESSIONS_KEY, {})
        pending = sessions.setdefault(pending_id, [])
        pending.append(str(local_path))
        count = len(pending)

        await message.reply_text(
            f"✅ Got it ({count} file{'s' if count > 1 else ''} so far). "
            "Send more or /done when finished."
        )
        return True

    # ---------- Firefly III integration ----------

    async def attach_to_transaction(
        self,
        journal_id: str,
        local_paths: list[str],
    ) -> tuple[int, int, list[str]]:
        """
        Registers pending files with Firefly III, uploads their bytes, and
        deletes the temporary local copies after successful upload.

        Returns (success_count, fail_count, failed_paths). Successfully uploaded
        files are deleted immediately; failed files are kept for retry.
        """
        if not local_paths:
            return 0, 0, []

        success, fail = 0, 0
        failed_paths: list[str] = []

        async with httpx.AsyncClient(
            base_url=FIREFLY_BASE_URL,
            headers={"Authorization": f"Bearer {FIREFLY_TOKEN}"},
            timeout=30.0,
        ) as client:
            for path_str in local_paths:
                src = Path(path_str)
                if not src.exists():
                    fail += 1
                    failed_paths.append(path_str)
                    continue

                try:
                    # 1. Register attachment entry
                    reg_resp = await client.post(
                        "/api/v1/attachments",
                        json={
                            "attachable_type": "TransactionJournal",
                            "attachable_id": int(journal_id),
                            "filename": src.name,
                            "title": f"Bill proof - {src.stem}",
                        },
                    )
                    reg_resp.raise_for_status()
                    upload_url = reg_resp.json()["data"]["attributes"]["upload_url"]

                    # 2. Upload file bytes
                    with open(src, "rb") as f:
                        file_bytes = f.read()

                    upload_resp = await client.post(
                        upload_url,
                        content=file_bytes,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    upload_resp.raise_for_status()

                    # 3. Success: delete the local temporary file immediately
                    src.unlink()
                    logger.info(
                        f"[Attachments] Uploaded and deleted local copy of {src.name}"
                    )
                    success += 1

                except Exception as e:
                    logger.error(f"[Attachments] Failed to process {src.name}: {e}")
                    fail += 1
                    failed_paths.append(str(src))

        # Clean up the now-empty per-transaction dir if possible
        try:
            src_parent = Path(local_paths[0]).parent
            if src_parent.exists() and not any(src_parent.iterdir()):
                src_parent.rmdir()
        except OSError:
            pass

        return success, fail, failed_paths


# Helpers

def _unique_ts() -> str:
    """Timestamp plus a random suffix so two files in the same second
    cannot overwrite each other."""
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _mime_to_ext(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime, ".bin")


def _done_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done adding files", callback_data="attach_done")]
    ])
