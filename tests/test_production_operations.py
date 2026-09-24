from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from scripts import backup_production
from scripts.check_backup_freshness import latest_snapshot_age
from scripts.check_production import validate_domain
from scripts.verify_restored_backup import verify


def test_production_settings_read_mounts_without_environment_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name, value in (
        ("postgres_runtime_password", "database-secret"),
        ("storage_access_key", "storage-user"),
        ("storage_secret_key", "storage-secret"),
    ):
        (tmp_path / name).write_text(value, encoding="utf-8")
    settings = Settings(  # type: ignore[call-arg]
        _secrets_dir=tmp_path,
        app_env="production",
        auth_public_origin="https://study.example.org",
        redis_enabled=True,
        redis_password=SecretStr("redis-secret"),
    )
    assert settings.postgres_runtime_password == SecretStr("database-secret")
    assert settings.storage_access_key == "storage-user"
    assert settings.storage_secret_key == SecretStr("storage-secret")


@pytest.mark.parametrize("domain", ["localhost", "127.0.0.1", "study.example.com", "bad/path"])
def test_production_domain_must_be_public_dns_name(domain: str) -> None:
    with pytest.raises(ValueError):
        validate_domain(domain)


def test_backup_age_fails_when_no_snapshot_exists() -> None:
    with pytest.raises(ValueError, match="No production backup"):
        latest_snapshot_age(b"[]", now=datetime.now(UTC))


def test_backup_age_uses_latest_tagged_snapshot() -> None:
    now = datetime.now(UTC)
    snapshots = [
        {"time": (now - timedelta(hours=27)).isoformat(), "tags": ["review-agent-production"]},
        {"time": now.isoformat(), "tags": ["unrelated"]},
    ]
    assert latest_snapshot_age(json.dumps(snapshots).encode(), now=now) == timedelta(hours=27)


@pytest.mark.parametrize(
    "repository", ["/var/backups", "s3:http://localhost:9000/backup", "sftp:localhost:/backup"]
)
def test_backup_rejects_local_repository(repository: str) -> None:
    with pytest.raises(backup_production.BackupError):
        backup_production.validate_remote_repository(repository)


def test_restored_snapshot_detects_changed_database(tmp_path: Path) -> None:
    source = tmp_path / "private" / "staging" / "review-agent-backup-once"
    source.mkdir(parents=True)
    manifest = source / "review-agent-backup-manifest.json"
    (source / "database.dump").write_bytes(b"changed")
    manifest.write_text(
        json.dumps(
            {
                "format": 1,
                "source_revision": "a" * 40,
                "database_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="archive is missing or changed"):
        verify(tmp_path)


@pytest.mark.parametrize(
    "fail_step", [None, "database dump", "pause writes", "encrypted remote snapshot"]
)
def test_backup_resumes_services_after_success_or_interrupted_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_step: str | None
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    staging.chmod(0o700)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = tmp_path / "production.env"
    env_file.write_text(
        f"PUBLIC_DOMAIN=study.example.org\nPROD_SECRETS_DIR={secrets}", encoding="utf-8"
    )
    env_file.chmod(0o600)
    password = tmp_path / "restic-password"
    password.write_text("backup-password", encoding="utf-8")
    password.chmod(0o600)
    monkeypatch.setenv("RESTIC_REPOSITORY", "sftp:backup.example.org:/backups/review-agent")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(password))
    monkeypatch.setenv("PROD_SECRETS_DIR", str(secrets))
    monkeypatch.setattr(backup_production.os, "geteuid", lambda: 0)
    monkeypatch.setattr(backup_production, "require_private_file", lambda _: None)
    monkeypatch.setattr(backup_production, "require_private_directory", lambda _: None)
    monkeypatch.setattr(backup_production, "volume_path", lambda _: tmp_path)
    monkeypatch.setattr(
        backup_production.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    steps: list[str] = []

    def fake_step(name: str, _: list[str], *, stdout: object = None) -> bytes:
        steps.append(name)
        if name == fail_step:
            raise backup_production.BackupError("simulated failure")
        if name == "list services":
            return b"edge api worker minio db redis"
        if name == "read deployed revision":
            return b"a" * 40
        if name == "database dump":
            assert stdout is not None
            stdout.write(b"PGDMP test archive")  # type: ignore[attr-defined]
        if name == "encrypted remote snapshot":
            assert any(staging.rglob("review-agent-backup-manifest.json"))
        return b""

    monkeypatch.setattr(backup_production, "run_step", fake_step)
    if fail_step:
        with pytest.raises(backup_production.BackupError):
            backup_production.backup(env_file=env_file, staging_dir=staging, project="test")
        if fail_step != "encrypted remote snapshot":
            assert "encrypted remote snapshot" not in steps
    else:
        backup_production.backup(env_file=env_file, staging_dir=staging, project="test")
        assert "encrypted remote snapshot" in steps
    assert "resume services" in steps
    assert steps.index("resume services") > steps.index("pause writes")
