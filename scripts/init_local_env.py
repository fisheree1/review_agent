from __future__ import annotations

import os
import secrets
from pathlib import Path

TEMPLATE_PATH = Path(".env.example")
OUTPUT_PATH = Path(".env")
PASSWORD_PREFIX = "POSTGRES_PASSWORD="


def build_local_env(template: str) -> str:
    password = secrets.token_hex(32)
    lines = template.splitlines()
    matches = [index for index, line in enumerate(lines) if line.startswith(PASSWORD_PREFIX)]

    if matches != [len(lines) - 1]:
        raise ValueError(".env.example must end with exactly one POSTGRES_PASSWORD entry")

    lines[matches[0]] = f"{PASSWORD_PREFIX}{password}"
    return "\n".join(lines) + "\n"


def main() -> None:
    content = build_local_env(TEMPLATE_PATH.read_text(encoding="utf-8"))
    try:
        descriptor = os.open(OUTPUT_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env already exists; refusing to overwrite it.") from None

    with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
        env_file.write(content)

    print("Created .env with a random local PostgreSQL password and mode 0600.")


if __name__ == "__main__":
    main()
