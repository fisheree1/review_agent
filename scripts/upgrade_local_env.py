from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

ENV_PATH = Path(".env")
LEGACY_TO_ADMIN_KEYS = {
    "POSTGRES_USER": "POSTGRES_ADMIN_USER",
    "POSTGRES_PASSWORD": "POSTGRES_ADMIN_PASSWORD",
}
NEW_LOCAL_VALUES = {
    "POSTGRES_MIGRATOR_USER": "review_agent_migrator",
    "POSTGRES_RUNTIME_USER": "review_agent_app",
    "MAX_UPLOAD_BYTES": "26214400",
    "STORAGE_ENDPOINT": "localhost:9000",
    "STORAGE_BUCKET": "review-agent-documents",
    "STORAGE_SECURE": "false",
    "JOB_POLL_SECONDS": "1",
    "JOB_LEASE_SECONDS": "120",
    "PDF_PARSER_TIMEOUT_SECONDS": "60",
    "MAX_PDF_PAGES": "500",
    "MAX_PDF_CHARACTERS": "5000000",
    "OFFICE_PARSER_TIMEOUT_SECONDS": "60",
    "MAX_OFFICE_UNITS": "1000",
    "MAX_OFFICE_CHARACTERS": "5000000",
    "MAX_OFFICE_UNCOMPRESSED_BYTES": "104857600",
}
NEW_LOCAL_GENERATORS: dict[str, Callable[[], str]] = {
    "POSTGRES_MIGRATOR_PASSWORD": lambda: secrets.token_hex(32),
    "POSTGRES_RUNTIME_PASSWORD": lambda: secrets.token_hex(32),
    "LOCAL_API_TOKEN": lambda: secrets.token_urlsafe(32),
    "LOCAL_WORKSPACE_ID": lambda: str(uuid4()),
    "STORAGE_ADMIN_ACCESS_KEY": lambda: secrets.token_hex(16),
    "STORAGE_ADMIN_SECRET_KEY": lambda: secrets.token_hex(32),
    "STORAGE_ACCESS_KEY": lambda: secrets.token_hex(16),
    "STORAGE_SECRET_KEY": lambda: secrets.token_hex(32),
}


def _env_entries(lines: list[str]) -> dict[str, tuple[int, str]]:
    entries: dict[str, tuple[int, str]] = {}
    for index, line in enumerate(lines):
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in entries:
            raise ValueError(f".env contains duplicate {key} entries")
        entries[key] = (index, value)
    return entries


def upgrade_local_env(content: str) -> str:
    lines = content.splitlines()
    entries = _env_entries(lines)

    for legacy_key, admin_key in LEGACY_TO_ADMIN_KEYS.items():
        legacy_entry = entries.get(legacy_key)
        admin_entry = entries.get(admin_key)
        if legacy_entry is not None and admin_entry is not None:
            if legacy_entry[1] != admin_entry[1]:
                raise ValueError(f".env contains conflicting {legacy_key} and {admin_key}")
            lines[legacy_entry[0]] = ""
        elif legacy_entry is not None:
            lines[legacy_entry[0]] = f"{admin_key}={legacy_entry[1]}"
        elif admin_entry is None:
            raise ValueError(
                f".env is missing {admin_key}; cannot identify the existing database owner"
            )

        entries = _env_entries(lines)

    if not entries["POSTGRES_ADMIN_PASSWORD"][1]:
        raise ValueError("POSTGRES_ADMIN_PASSWORD must not be blank")

    for key, default_value in NEW_LOCAL_VALUES.items():
        if key in entries:
            if not entries[key][1]:
                raise ValueError(f"{key} must not be blank")
            continue
        lines.append(f"{key}={default_value}")
        entries = _env_entries(lines)

    for key, generate_value in NEW_LOCAL_GENERATORS.items():
        if key in entries:
            if not entries[key][1]:
                raise ValueError(f"{key} must not be blank")
            continue
        lines.append(f"{key}={generate_value()}")
        entries = _env_entries(lines)

    return "\n".join(line for line in lines if line) + "\n"


def main() -> None:
    original = ENV_PATH.read_text(encoding="utf-8")
    upgraded = upgrade_local_env(original)
    if upgraded == original:
        print("Local .env already uses separate PostgreSQL roles.")
        return

    temporary_path = ENV_PATH.with_name(".env.database-upgrade.tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
            env_file.write(upgraded)
        os.replace(temporary_path, ENV_PATH)
        os.chmod(ENV_PATH, 0o600)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    print("Upgraded .env with document-ingestion credentials and mode 0600.")


if __name__ == "__main__":
    main()
