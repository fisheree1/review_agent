"""Fail closed on unsafe single-server production configuration."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from scripts.backup_production import COMPOSE_FILE, BackupError, validate_remote_repository
from scripts.init_production_secrets import (
    CONFIG_NAMES,
    GENERATED_NAMES,
    MODEL_NAMES,
    redis_acl_content,
)

DOMAIN = re.compile(
    r"(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or not key.isidentifier() or key in values:
            raise ValueError(f"Invalid entry in {path.name}")
        values[key] = value.strip()
    return values


def validate_domain(value: str) -> None:
    if not DOMAIN.fullmatch(value) or value.endswith(".example.com"):
        raise ValueError("PUBLIC_DOMAIN must be a real lowercase DNS name")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("PUBLIC_DOMAIN must be a DNS name, not an IP address")


def private(path: Path, mode: int, *, group: int | None = None) -> None:
    details = path.lstat()
    if (
        (not stat.S_ISREG(details.st_mode) and not stat.S_ISDIR(details.st_mode))
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) != mode
    ):
        raise ValueError(f"{path.name} must be root-owned with mode {mode:04o}")
    if group is not None and details.st_gid != group:
        raise ValueError(f"{path.name} must have group {group}")


def check(prod_env: Path, backup_env: Path) -> None:
    private(prod_env, 0o600)
    private(backup_env, 0o600)
    production = parse_env(prod_env)
    backup = parse_env(backup_env)
    forbidden = [
        key
        for key in production
        if "PASSWORD" in key
        or ("SECRET" in key and key != "PROD_SECRETS_DIR")
        or key.endswith("API_KEY")
    ]
    if forbidden:
        raise ValueError("Production configuration contains secrets; use secret files")
    validate_domain(production.get("PUBLIC_DOMAIN", ""))
    secrets_dir = Path(production.get("PROD_SECRETS_DIR", ""))
    if not secrets_dir.is_absolute():
        raise ValueError("PROD_SECRETS_DIR must be an absolute path")
    private(secrets_dir, 0o700)
    for name in (*GENERATED_NAMES, *MODEL_NAMES, *CONFIG_NAMES):
        path = secrets_dir / name
        private(path, 0o640, group=10001)
        if not path.read_bytes():
            raise ValueError(f"{name} is empty")
    if (secrets_dir / "redis_acl").read_text(encoding="utf-8") != redis_acl_content(
        (secrets_dir / "redis_password").read_text(encoding="utf-8")
    ):
        raise ValueError("Redis ACL does not match its restricted credential")
    if backup.get("PROD_SECRETS_DIR") != str(secrets_dir):
        raise ValueError("Backup and application secret directories differ")
    try:
        validate_remote_repository(backup.get("RESTIC_REPOSITORY", ""))
    except BackupError as exc:
        raise ValueError(str(exc)) from exc
    restic_password = Path(backup.get("RESTIC_PASSWORD_FILE", ""))
    staging = Path(backup.get("BACKUP_STAGING_DIR", ""))
    if not restic_password.is_absolute() or not staging.is_absolute():
        raise ValueError("Backup paths must be absolute")
    private(restic_password, 0o600)
    private(staging, 0o700)

    repository_check = subprocess.run(
        ["restic", "snapshots", "--json"],
        capture_output=True,
        env={**os.environ, **backup},
        check=False,
    )
    if repository_check.returncode:
        raise ValueError("Off-server backup repository is not accessible")

    environment = {**os.environ, **production}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(prod_env),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        env=environment,
        check=False,
    )
    if result.returncode:
        raise ValueError("Production Compose configuration is invalid")
    services = json.loads(result.stdout)["services"]
    exposed = {name for name, service in services.items() if service.get("ports")}
    if exposed != {"edge"}:
        raise ValueError("Only the HTTPS edge may publish ports")
    api_env = services["api"]["environment"]
    if (
        api_env.get("APP_ENV") != "production"
        or api_env.get("AUTH_PUBLIC_ORIGIN") != ("https://" + production["PUBLIC_DOMAIN"])
        or api_env.get("REDIS_ENABLED") != "true"
    ):
        raise ValueError("API production identity or HTTPS origin is misconfigured")
    if any("PASSWORD" in key or "SECRET" in key or key.endswith("API_KEY") for key in api_env):
        raise ValueError("API credentials must not be environment variables")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--backup-env-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.env_file, args.backup_env_file)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Production preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("Production configuration and backup prerequisites passed.")


if __name__ == "__main__":
    main()
