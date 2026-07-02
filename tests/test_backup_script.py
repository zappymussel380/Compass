"""Exercises scripts/backup.sh end-to-end with stubbed docker/gpg/rclone/curl
binaries, so no containers, keys, or network are needed."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "backup.sh"


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    """Isolated COMPASS_DIR with stub binaries on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"

    _write_stub(bin_dir, "docker", 'echo "$@" >> "$CALLS_LOG"; echo "-- fake sql dump --"')
    # gpg stub: log args and create the -o output file
    _write_stub(bin_dir, "gpg", (
        'echo "$@" >> "$CALLS_LOG"\n'
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
        '[ -n "$out" ] && echo fake-encrypted > "$out"'
    ))
    _write_stub(bin_dir, "rclone", 'echo "rclone $@" >> "$CALLS_LOG"')
    _write_stub(bin_dir, "curl", 'echo "curl-called" >> "$CALLS_LOG"')

    compass_dir = tmp_path / "compass"
    compass_dir.mkdir()
    (compass_dir / "docker-compose.yml").write_text("name: test\n")
    for f in ("README.md", ".env.example", ".gitignore"):
        (compass_dir / f).write_text("stub\n")
    for d in ("docs", "scripts", "bot", "openwebui"):
        (compass_dir / d).mkdir()
    passphrase = compass_dir / ".backup_passphrase"
    passphrase.write_text("secret\n")
    (compass_dir / ".env").write_text(
        "TELEGRAM_TOKEN=tok123\nTELEGRAM_ALLOWED_USER_IDS=42,43\n"
    )

    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "COMPASS_DIR": str(compass_dir),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "PASSPHRASE_FILE": str(passphrase),
        "HOME": os.environ.get("HOME", "/root"),
    }, log, tmp_path


def _run(env_map, extra=None):
    e = dict(env_map)
    e.update(extra or {})
    return subprocess.run(
        ["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=60
    )


def test_backup_produces_encrypted_files_only(env):
    env_map, log, tmp_path = env
    result = _run(env_map)
    assert result.returncode == 0, result.stderr

    backups = list(Path(env_map["BACKUP_DIR"]).iterdir())
    names = sorted(p.name for p in backups)
    assert all(n.endswith(".gpg") for n in names), names
    # firefly dump, vikunja dump, config archive
    assert len(names) == 3
    assert "Backup complete: 3 encrypted files" in result.stdout


def test_passphrase_never_on_gpg_command_line(env):
    env_map, log, _ = env
    _run(env_map)
    gpg_calls = [l for l in log.read_text().splitlines() if "--passphrase" in l]
    assert gpg_calls, "gpg was never called"
    for call in gpg_calls:
        assert "secret" not in call
        assert "--passphrase-file" in call


def test_rclone_retention_is_scoped_to_gpg(env):
    env_map, log, _ = env
    _run(env_map, {"RCLONE_REMOTE": "remote:backups"})
    lines = log.read_text().splitlines()
    delete_lines = [l for l in lines if l.startswith("rclone delete")]
    assert delete_lines, "rclone delete was never called"
    assert "--include *.gpg" in delete_lines[0]


def test_rclone_skipped_without_remote(env):
    env_map, log, _ = env
    _run(env_map)
    rclone_lines = [l for l in log.read_text().splitlines() if l.startswith("rclone")]
    assert rclone_lines == []


def test_missing_passphrase_file_aborts(env):
    env_map, _, _ = env
    result = _run(env_map, {"PASSPHRASE_FILE": "/nonexistent"})
    assert result.returncode != 0
    assert "Missing passphrase file" in result.stdout


def test_failure_sends_telegram_notification(env):
    env_map, log, tmp_path = env
    # Make docker fail so the ERR trap fires
    _write_stub(Path(tmp_path / "bin"), "docker", "exit 1")
    result = _run(env_map)
    assert result.returncode != 0
    assert "curl-called" in log.read_text()
    assert "Backup failed" in result.stdout
