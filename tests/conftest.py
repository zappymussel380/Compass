"""Shared test setup.

The bot modules read configuration from the environment at import time, so
dummy values are injected here before anything under bot/ is imported.
No test talks to a real service.
"""

import os
import sys
import tempfile

BOT_DIR = os.path.join(os.path.dirname(__file__), "..", "bot")
sys.path.insert(0, os.path.abspath(BOT_DIR))

os.environ.setdefault("TELEGRAM_TOKEN", "0000000000:TEST-TOKEN")
os.environ.setdefault("TELEGRAM_ALLOWED_USER_IDS", "1")
os.environ.setdefault("TELEGRAM_ALLOWED_CHAT_IDS", "")
os.environ.setdefault("FIREFLY_URL", "http://firefly.test")
os.environ.setdefault("FIREFLY_TOKEN", "test-firefly-token")
os.environ.setdefault("VIKUNJA_URL", "http://vikunja.test")
os.environ.setdefault("VIKUNJA_TOKEN", "test-vikunja-token")
os.environ.setdefault("OLLAMA_URL", "http://ollama.test")
os.environ.setdefault("OLLAMA_MODEL", "test-model")
os.environ.setdefault("ATTACHMENTS_DIR", tempfile.mkdtemp(prefix="compass-test-attachments-"))
os.environ.setdefault("TZ", "Asia/Kolkata")


class FakeResponse:
    """Minimal stand-in for requests.Response used by the client tests."""

    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = ""

    def json(self):
        return self._json
