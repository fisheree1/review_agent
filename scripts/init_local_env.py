from __future__ import annotations

import os
import secrets
from pathlib import Path

TEMPLATE_PATH = Path(".env.example")
OUTPUT_PATH = Path(".env")
PASSWORD_KEYS = (
    "POSTGRES_ADMIN_PASSWORD",
    "POSTGRES_MIGRATOR_PASSWORD",
    "POSTGRES_RUNTIME_PASSWORD",
)


def build_local_env(template: str) -> str:
    lines = template.splitlines()
    for key in PASSWORD_KEYS:
        prefix = f"{key}="
        matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
        if len(matches) != 1 or lines[matches[0]] != prefix:
            raise ValueError(f".env.example must contain exactly one blank {key} entry")
        lines[matches[0]] = f"{prefix}{secrets.token_hex(32)}"

    return "\n".join(lines) + "\n"


def main() -> None:
    content = build_local_env(TEMPLATE_PATH.read_text(encoding="utf-8"))
    try:
        descriptor = os.open(OUTPUT_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env already exists; refusing to overwrite it.") from None

    with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
        env_file.write(content)

    print("Created .env with separate random PostgreSQL passwords and mode 0600.")


if __name__ == "__main__":
    main()
