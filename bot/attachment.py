"""
attachment.py
-------------
Handles bill/proof file attachments on Compass transactions.

Flow:
  1. After transaction is staged (pending confirm), user taps 📎 Attach File
  2. Bot prompts for files - user sends PDFs / images one by one
  3. User sends /done (or taps Done button) to finalise
  4. On confirm, files are:
     a. Saved temporarily under ATTACHMENTS_DIR/pending/
     b. Registered and uploaded to Firefly III via the Attachments API
     c. Deleted locally after upload

Supported formats: PDF, JPG, JPEG, PNG, WEBP
Max file size: 20MB (Telegram Bot API limit for documents)
"""

import os
import logging
from pathlib import Path
from datetime import datetime

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


# -- Session state key ---------------------------------------------
# Stored in context.user_data during the attachment collection phase
ATTACHMENT_SESSION_KEY = "pending_attachments"  # list of local file paths
AWAITING_ATTACHMENT_KEY = "awaiting_attachment"  # bool flag


class AttachmentHandler:
    """
    Manages the full lifecycle of transaction attachments.
    Instantiate once and register handlers in your bot setup.
    """

    def __init__(self):
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"[Attachments] Storage root: {ATTACHMENTS_DIR}")

    # Telegram handlers

    async def prompt_for_files(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Called when user taps the 📎 Attach File button on the confirm keyboard.
        Sets the AWAITING_ATTACHMENT flag and waits for incoming files.
        """
        context.user_data[AWAITING_ATTACHMENT_KEY] = True
        context.user_data[ATTACHMENT_SESSION_KEY] = []

        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📎 Send me the bill(s) now - PDF or image files, one at a time.\n"
            "When you're done, send /done or tap the button below.",
            reply_markup=_done_keyboard(),
        )

    async def handle_incoming_file(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """
        Receives a document or photo from the user during attachment phase.
        Returns True if the file was accepted, False if ignored (wrong state).

        Wire this into your MessageHandler before the normal message router,
        checking AWAITING_ATTACHMENT_KEY first.
        """
        if not context.user_data.get(AWAITING_ATTACHMENT_KEY):
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
            filename = f"bill_{_ts()}{ext}"

        # Photo (compressed image - acceptable for receipts)
        elif message.photo:
            photo = message.photo[-1]  # highest resolution
            file_obj = await photo.get_file()
            filename = f"bill_{_ts()}.jpg"
            mime_type = "image/jpeg"

        else:
            return False  # not a file message - let other handlers deal with it

        # Download to a temp location (txn not yet confirmed, no journal_id yet)
        temp_dir = ATTACHMENTS_DIR / "pending" / str(update.effective_user.id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        local_path = temp_dir / filename

        await file_obj.download_to_drive(local_path)
        logger.info(f"[Attachments] Saved pending file: {local_path}")

        # Track in session
        pending: list = context.user_data[ATTACHMENT_SESSION_KEY]
        pending.append(str(local_path))
        count = len(pending)

        await message.reply_text(
            f"✅ Got it ({count} file{'s' if count > 1 else ''} so far). "
            "Send more or /done when finished."
        )
        return True

    async def handle_done_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        if not context.user_data.get(AWAITING_ATTACHMENT_KEY):
            return False

        # Simply clear the flag; bot.py will handle the UI refresh
        context.user_data[AWAITING_ATTACHMENT_KEY] = False
        return True

    def get_pending_files(self, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
        """Returns list of local paths for files collected this session."""
        return context.user_data.get(ATTACHMENT_SESSION_KEY, [])

    def set_pending_files(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        paths: list[str],
    ) -> None:
        """Replace the pending attachment list without deleting the files."""
        if paths:
            context.user_data[ATTACHMENT_SESSION_KEY] = paths
            context.user_data[AWAITING_ATTACHMENT_KEY] = False
        else:
            context.user_data.pop(ATTACHMENT_SESSION_KEY, None)
            context.user_data.pop(AWAITING_ATTACHMENT_KEY, None)

    def clear_pending_files(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Wipes all physical traces of a transaction's attachments."""
        # 1. Pop the paths and state flags (Indented 8 spaces)
        paths = context.user_data.pop(ATTACHMENT_SESSION_KEY, [])
        context.user_data.pop(AWAITING_ATTACHMENT_KEY, None)

        # 2. Delete every individual file first
        for p in paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"Failed to delete file {p}: {e}")

        # 3. Now, try to delete the user's temp folder (Must be INSIDE the function)
        if paths:
            try:
                # We derive the folder path from the first file's parent
                temp_dir = Path(paths[0]).parent
                if temp_dir.exists() and "pending" in temp_dir.parts:
                    temp_dir.rmdir() # This only succeeds if the folder is empty
                    logger.info(f"Successfully removed temp directory: {temp_dir}")
            except Exception:
                # We catch exceptions because other concurrent uploads 
                # might still have the folder open.
                pass

    # ------Firefly III integration ---------------------------------------

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

                    # 3. SUCCESS: Delete the local temporary file immediately
                    src.unlink() 
                    logger.info(f"[Attachments] Successfully uploaded and deleted local copy of {src.name}")
                    success += 1

                except Exception as e:
                    logger.error(f"[Attachments] Failed to process {src.name}: {e}")
                    fail += 1
                    failed_paths.append(str(src))

        # Clean up now-empty pending dir if possible
        try:
            src_parent = Path(local_paths[0]).parent
            if src_parent.exists() and not any(src_parent.iterdir()):
                src_parent.rmdir()
        except Exception:
            pass

        return success, fail, failed_paths


# Helpers

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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
