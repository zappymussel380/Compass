"""Per-user config loading and legacy migration."""

import json
import os
import stat

import pytest

import users


def write_user(dirpath, tg_id, **over):
    cfg = {
        "telegram_id": tg_id,
        "name": f"user-{tg_id}",
        "firefly_token": f"ff-{tg_id}",
        "vikunja_token": f"vk-{tg_id}",
        "accounts": {"Bank %d" % tg_id: ["bank"]},
    }
    cfg.update(over)
    path = os.path.join(dirpath, f"{tg_id}.json")
    with open(path, "w") as f:
        json.dump(cfg, f)
    return path


def test_loads_users_from_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path))
    write_user(tmp_path, 111)
    write_user(tmp_path, 222, name="Second")
    loaded = users.load_users()
    assert set(loaded) == {111, 222}
    assert loaded[222].name == "Second"
    assert loaded[111].firefly_token == "ff-111"


def test_account_maps_are_isolated_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path))
    write_user(tmp_path, 111, accounts={"Alice Bank": ["bank", "alice"]})
    write_user(tmp_path, 222, accounts={"Bob Card": ["card"]})
    loaded = users.load_users()
    assert loaded[111].resolver.resolve("alice") == ("match", "Alice Bank")
    assert loaded[222].resolver.resolve("alice") == ("unknown", None)
    assert "Bob Card" not in loaded[111].account_choices


def test_invalid_user_file_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path))
    write_user(tmp_path, 111)
    (tmp_path / "999.json").write_text("{not json")
    (tmp_path / "888.json").write_text(json.dumps({"telegram_id": 888}))  # missing tokens
    loaded = users.load_users()
    assert set(loaded) == {111}


def test_legacy_env_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("FIREFLY_TOKEN", "legacy-ff")
    monkeypatch.setenv("VIKUNJA_TOKEN", "legacy-vk")
    loaded = users.load_users()
    assert set(loaded) == {42}
    assert loaded[42].firefly_token == "legacy-ff"
    # migration materializes a per-user file with owner-only permissions
    path = tmp_path / "42.json"
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    materialized = json.loads(path.read_text())
    assert materialized["firefly_token"] == "legacy-ff"
    assert materialized["vikunja_token"] == "legacy-vk"


def test_user_files_win_over_legacy_env(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("FIREFLY_TOKEN", "legacy-ff")
    monkeypatch.setenv("VIKUNJA_TOKEN", "legacy-vk")
    write_user(tmp_path, 7)
    loaded = users.load_users()
    assert set(loaded) == {7}  # env config ignored once files exist


def test_no_config_yields_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("FIREFLY_TOKEN", "")
    monkeypatch.setenv("VIKUNJA_TOKEN", "")
    assert users.load_users() == {}
