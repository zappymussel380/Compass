from datetime import date, datetime

import pytest

from conftest import FakeResponse
from vikunja_client import VikunjaClient, VikunjaError


@pytest.fixture
def client():
    c = VikunjaClient(base_url="http://vikunja.test", token="t")
    c._project_cache = {"Inbox": 1, "Work": 2, "Personal": 3}
    return c


# ---------- _to_rfc3339 ----------

def test_rfc3339_from_date_only_string():
    assert VikunjaClient._to_rfc3339("2026-05-02") == "2026-05-02T00:00:00Z"


def test_rfc3339_passthrough_with_timezone():
    assert VikunjaClient._to_rfc3339("2026-05-02T10:00:00Z") == "2026-05-02T10:00:00Z"
    assert VikunjaClient._to_rfc3339("2026-05-02T10:00:00+05:30") == "2026-05-02T10:00:00+05:30"


def test_rfc3339_naive_datetime_string_gets_z():
    assert VikunjaClient._to_rfc3339("2026-05-02T10:00:00") == "2026-05-02T10:00:00Z"


def test_rfc3339_from_objects():
    assert VikunjaClient._to_rfc3339(date(2026, 5, 2)) == "2026-05-02T00:00:00Z"
    assert VikunjaClient._to_rfc3339(datetime(2026, 5, 2, 9, 30)) == "2026-05-02T09:30:00Z"


def test_rfc3339_rejects_other_types():
    with pytest.raises(ValueError):
        VikunjaClient._to_rfc3339(12345)


# ---------- projects ----------

def test_resolve_project_exact_and_alias(client):
    assert client.resolve_project("Work") == 2
    assert client.resolve_project("office") == 2
    assert client.resolve_project("personal") == 3


def test_resolve_project_unknown(client):
    with pytest.raises(VikunjaError, match="Unknown project"):
        client.resolve_project("gardening")


# ---------- create_task ----------

def test_create_task_with_recurrence(monkeypatch, client):
    captured = {}

    def fake_request(method, path, *, action, timeout=10, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = kwargs["json"]
        return FakeResponse({"id": 42})

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.create_task({
        "title": "Pay credit card bill",
        "project": "Personal",
        "due_date": "2026-07-05",
        "priority": 2,
        "recurrence": {"interval_days": 30, "mode": "monthly"},
    })

    assert result == {"id": 42}
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/v1/projects/3/tasks"
    payload = captured["json"]
    assert payload["due_date"] == "2026-07-05T00:00:00Z"
    assert payload["priority"] == 2
    assert payload["repeat_after"] == 30 * 86400
    assert payload["repeat_mode"] == 1  # monthly


def test_create_task_without_recurrence_or_date(monkeypatch, client):
    captured = {}

    def fake_request(method, path, *, action, timeout=10, **kwargs):
        captured["json"] = kwargs["json"]
        return FakeResponse({"id": 7})

    monkeypatch.setattr(client, "_request", fake_request)
    client.create_task({"title": "Call mom", "project": "Personal",
                        "priority": 0, "recurrence": None})
    payload = captured["json"]
    assert "repeat_after" not in payload
    assert "due_date" not in payload


# ---------- update / done ----------

def test_mark_done_merges_full_task(monkeypatch, client):
    calls = []

    def fake_request(method, path, *, action, timeout=10, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if method == "GET":
            return FakeResponse({"id": 5, "title": "T", "done": False})
        return FakeResponse({"id": 5, "title": "T", "done": True})

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.mark_done(5)
    assert result["done"] is True
    # POST body must carry the fetched task with done flipped
    post = [c for c in calls if c[0] == "POST"][0]
    assert post[2]["done"] is True
    assert post[2]["title"] == "T"


# ---------- list_tasks ----------

def test_list_tasks_filters_done_and_inbox_and_paginates(monkeypatch, client):
    def fake_request(method, path, *, action, timeout=10, **kwargs):
        page = kwargs["params"]["page"]
        if "/projects/2/" in path:  # Work: two pages
            if page == 1:
                return FakeResponse(
                    [{"id": i, "done": i % 2 == 0} for i in range(100)]
                )
            return FakeResponse([{"id": 100, "done": False}])
        if "/projects/3/" in path:  # Personal
            return FakeResponse([{"id": 200, "done": True}])
        raise AssertionError(f"Inbox should not be queried: {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    tasks = client.list_tasks()
    ids = [t["id"] for t in tasks]
    assert 100 in ids                      # second page reached
    assert all(not t["done"] for t in tasks)
    assert 200 not in ids                  # done task excluded


def test_list_tasks_project_filter(monkeypatch, client):
    seen = []

    def fake_request(method, path, *, action, timeout=10, **kwargs):
        seen.append(path)
        return FakeResponse([])

    monkeypatch.setattr(client, "_request", fake_request)
    client.list_tasks(project_filter="work")
    assert seen == ["/api/v1/projects/2/tasks"]
