"""Create one encrypted, off-server snapshot of the database and object store.

Run as root on the single-server Compose host. API, web, worker, MinIO and the
public proxy pause while the PostgreSQL dump and MinIO volume are captured.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPOSITORY_ROOT / "compose.production.yaml"
REQUIRED_SERVICES = {"edge", "api", "worker", "minio", "db", "redis"}
PAUSED_SERVICES = ("edge", "api", "worker", "minio")
REMOTE_PREFIXES = ("s3:", "sftp:", "b2:", "azure:", "gs:", "rest:")


class BackupError(RuntimeError):
    """A backup prerequisite or step failed without exposing provider output."""


def run_step(name: str, args: list[str], *, stdout: IO[bytes] | None = None) -> bytes:
    result = subprocess.run(args, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise BackupError(f"{name} failed (exit {result.returncode})")
    return result.stdout or b""


def require_private_file(path: Path) -> None:
    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise BackupError(f"{path.name} must be a root-owned regular file (mode 0600)")


def require_private_directory(path: Path) -> None:
    details = path.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise BackupError(f"{path.name} must be a root-owned directory (mode 0700)")


def validate_remote_repository(repository: str) -> None:
    if not repository.startswith(REMOTE_PREFIXES):
        raise BackupError("RESTIC_REPOSITORY must point to an off-server repository")
    if repository.startswith(("s3:", "rest:")):
        host = urlsplit(repository.split(":", 1)[1]).hostname
    elif repository.startswith("sftp:"):
        host = repository[5:].split(":", 1)[0].rsplit("@", 1)[-1].strip("[]")
    else:
        host = None
    if host in {"localhost", "127.0.0.1", "::1", "minio", "db"}:
        raise BackupError("Backup repository must be outside this server")


def production_secret_path(env_file: Path) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key == "PROD_SECRETS_DIR":
            return value.strip()
    raise BackupError("Production environment has no PROD_SECRETS_DIR")


def volume_path(name: str) -> Path:
    raw = (
        run_step(
            "inspect volume", ["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", name]
        )
        .decode("utf-8")
        .strip()
    )
    path = Path(raw)
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise BackupError(f"Volume {name} has no safe local mountpoint")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup(*, env_file: Path, staging_dir: Path, project: str) -> None:
    if os.geteuid() != 0:
        raise BackupError("Run the production backup as root on the Docker host")
    validate_remote_repository(os.environ.get("RESTIC_REPOSITORY", ""))
    password_path = Path(os.environ.get("RESTIC_PASSWORD_FILE", ""))
    if not password_path.is_absolute():
        raise BackupError("RESTIC_PASSWORD_FILE must be an absolute path")
    require_private_file(password_path)
    require_private_file(env_file)
    require_private_directory(staging_dir)
    secrets_dir = Path(os.environ.get("PROD_SECRETS_DIR", ""))
    if not secrets_dir.is_absolute():
        raise BackupError("PROD_SECRETS_DIR must be an absolute path")
    require_private_directory(secrets_dir)
    if production_secret_path(env_file) != str(secrets_dir):
        raise BackupError("Backup and application must use the same secret directory")

    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "--env-file",
        str(env_file),
        "-f",
        str(COMPOSE_FILE),
    ]
    running = set(
        run_step("list services", [*compose, "ps", "--services", "--status", "running"])
        .decode()
        .split()
    )
    if not REQUIRED_SERVICES <= running:
        raise BackupError("All production services must be running before a backup")
    minio = volume_path(f"{project}_minio_data")
    caddy = volume_path(f"{project}_caddy_data")
    revision = (
        run_step("read deployed revision", ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"])
        .decode()
        .strip()
    )
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise BackupError("Deployment has no reproducible Git revision")

    paused = False
    try:
        paused = True
        run_step("pause writes", [*compose, "stop", *PAUSED_SERVICES])
        with tempfile.TemporaryDirectory(prefix="review-agent-backup-", dir=staging_dir) as temp:
            dump = Path(temp) / "database.dump"
            descriptor = os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                run_step(
                    "database dump",
                    [
                        *compose,
                        "exec",
                        "-T",
                        "db",
                        "sh",
                        "-c",
                        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl '
                        "-n review_agent -n review_agent_auth",
                    ],
                    stdout=output,
                )
            if dump.stat().st_size == 0:
                raise BackupError("Database dump is empty")
            with dump.open("rb") as source:
                result = subprocess.run(
                    [*compose, "exec", "-T", "db", "pg_restore", "--list"],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            if result.returncode != 0:
                raise BackupError("Database archive validation failed")
            manifest = Path(temp) / "review-agent-backup-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format": 1,
                        "created_at": datetime.now(UTC).isoformat(),
                        "project": project,
                        "source_revision": revision,
                        "database_sha256": sha256_file(dump),
                        "minio_volume": f"{project}_minio_data",
                        "minio_mountpoint": str(minio),
                        "caddy_mountpoint": str(caddy),
                        "secrets_directory": str(secrets_dir),
                        "production_env": str(env_file),
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            run_step(
                "encrypted remote snapshot",
                [
                    "restic",
                    "backup",
                    "--tag",
                    "review-agent-production",
                    str(dump),
                    str(manifest),
                    str(minio),
                    str(caddy),
                    str(secrets_dir),
                    str(env_file),
                ],
            )
    finally:
        if paused:
            run_step("resume services", [*compose, "up", "-d", "--wait", "--wait-timeout", "180"])
    run_step("verify backup repository", ["restic", "check"])
    print("Production backup completed and repository structure verified.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--project", default="review-agent-prod")
    args = parser.parse_args()
    try:
        with Path("/var/lock/review-agent-backup.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BackupError("Another backup is already running") from exc
            backup(
                env_file=args.env_file.resolve(),
                staging_dir=args.staging_dir.resolve(),
                project=args.project,
            )
    except (BackupError, OSError) as exc:
        print(f"Production backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
