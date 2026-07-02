import asyncio
from pathlib import Path

import pytest

import attachment
from attachment import (
    ATTACHMENT_SESSIONS_KEY,
    AWAITING_ATTACHMENT_KEY,
    AttachmentHandler,
)


@pytest.fixture
def handler():
    return AttachmentHandler()


# ---------- per-transaction session state ----------

def test_sessions_are_isolated_per_pending_id(handler):
    user_data = {}
    handler.set_pending_files(user_data, "aaa", ["/tmp/a.pdf"])
    handler.set_pending_files(user_data, "bbb", ["/tmp/b.pdf"])

    assert handler.get_pending_files(user_data, "aaa") == ["/tmp/a.pdf"]
    assert handler.get_pending_files(user_data, "bbb") == ["/tmp/b.pdf"]
    assert handler.get_pending_files(user_data, "ccc") == []


def test_set_empty_paths_clears_session(handler):
    user_data = {ATTACHMENT_SESSIONS_KEY: {"aaa": ["/tmp/a.pdf"]}}
    handler.set_pending_files(user_data, "aaa", [])
    assert handler.get_pending_files(user_data, "aaa") == []


def test_clear_deletes_files_and_awaiting_flag(handler, tmp_path):
    f = tmp_path / "bill.pdf"
    f.write_bytes(b"x")
    user_data = {
        ATTACHMENT_SESSIONS_KEY: {"aaa": [str(f)]},
        AWAITING_ATTACHMENT_KEY: "aaa",
    }
    handler.clear_pending_files(user_data, "aaa")
    assert not f.exists()
    assert AWAITING_ATTACHMENT_KEY not in user_data
    assert handler.get_pending_files(user_data, "aaa") == []


def test_clear_leaves_other_sessions_awaiting_flag(handler):
    user_data = {
        ATTACHMENT_SESSIONS_KEY: {"aaa": []},
        AWAITING_ATTACHMENT_KEY: "bbb",
    }
    handler.clear_pending_files(user_data, "aaa")
    assert user_data[AWAITING_ATTACHMENT_KEY] == "bbb"


def test_done_command_pops_and_returns_pid(handler):
    user_data = {AWAITING_ATTACHMENT_KEY: "aaa"}
    assert handler.handle_done_command(user_data) == "aaa"
    assert handler.handle_done_command(user_data) is None


# ---------- Firefly upload (mocked httpx) ----------

class FakeHTTPResponse:
    def __init__(self, json_data=None, fail=False):
        self._json = json_data or {}
        self._fail = fail

    def raise_for_status(self):
        if self._fail:
            raise RuntimeError("boom")

    def json(self):
        return self._json


class FakeAsyncClient:
    """Replaces httpx.AsyncClient; records uploads, can fail per-file."""

    fail_uploads_for: set = set()
    uploaded: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, content=None, headers=None):
        if url == "/api/v1/attachments":
            filename = json["filename"]
            if filename in self.fail_uploads_for:
                return FakeHTTPResponse(fail=True)
            return FakeHTTPResponse(
                {"data": {"attributes": {"upload_url": f"/upload/{filename}"}}}
            )
        FakeAsyncClient.uploaded.append(url)
        return FakeHTTPResponse()


@pytest.fixture
def fake_httpx(monkeypatch):
    FakeAsyncClient.fail_uploads_for = set()
    FakeAsyncClient.uploaded = []
    monkeypatch.setattr(attachment.httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


def test_successful_upload_deletes_local_files(handler, fake_httpx, tmp_path):
    f1 = tmp_path / "bill_1.pdf"
    f2 = tmp_path / "bill_2.pdf"
    f1.write_bytes(b"one")
    f2.write_bytes(b"two")

    ok, fail, failed = asyncio.run(
        handler.attach_to_transaction("77", [str(f1), str(f2)])
    )
    assert (ok, fail, failed) == (2, 0, [])
    assert not f1.exists() and not f2.exists()
    assert len(fake_httpx.uploaded) == 2


def test_failed_upload_keeps_file_for_retry(handler, fake_httpx, tmp_path):
    good = tmp_path / "bill_ok.pdf"
    bad = tmp_path / "bill_bad.pdf"
    good.write_bytes(b"g")
    bad.write_bytes(b"b")
    fake_httpx.fail_uploads_for = {"bill_bad.pdf"}

    ok, fail, failed = asyncio.run(
        handler.attach_to_transaction("77", [str(good), str(bad)])
    )
    assert ok == 1 and fail == 1
    assert failed == [str(bad)]
    assert not good.exists()      # success deleted
    assert bad.exists()           # failure kept for retry


def test_missing_file_counts_as_failure(handler, fake_httpx, tmp_path):
    ghost = tmp_path / "gone.pdf"
    ok, fail, failed = asyncio.run(
        handler.attach_to_transaction("77", [str(ghost)])
    )
    assert (ok, fail) == (0, 1)
    assert failed == [str(ghost)]


def test_no_files_is_a_noop(handler, fake_httpx):
    assert asyncio.run(handler.attach_to_transaction("77", [])) == (0, 0, [])
