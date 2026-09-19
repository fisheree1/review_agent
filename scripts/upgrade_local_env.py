from __future__ import annotations

import os
import secrets
from pathlib import Path

ENV_PATH = Path(".env")
LEGACY_TO_ADMIN_KEYS = {
    "POSTGRES_USER": "POSTGRES_ADMIN_USER",
    "POSTGRES_PASSWORD": "POSTGRES_ADMIN_PASSWORD",
}
NEW_LOCAL_VALUES = {
    "POSTGRES_MIGRATOR_USER": "review_agent_migrator",
    "POSTGRES_MIGRATOR_PASSWORD": None,
    "POSTGRES_RUNTIME_USER": "review_agent_app",
    "POSTGRES_RUNTIME_PASSWORD": None,
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
        value = default_value if default_value is not None else secrets.token_hex(32)
        lines.append(f"{key}={value}")
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

    print("Upgraded .env with separate PostgreSQL role credentials and mode 0600.")


if __name__ == "__main__":
    main()
