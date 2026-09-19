from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import uuid4

TEMPLATE_PATH = Path(".env.example")
OUTPUT_PATH = Path(".env")
GENERATED_VALUES = {
    "POSTGRES_ADMIN_PASSWORD": lambda: secrets.token_hex(32),
    "POSTGRES_MIGRATOR_PASSWORD": lambda: secrets.token_hex(32),
    "POSTGRES_RUNTIME_PASSWORD": lambda: secrets.token_hex(32),
    "LOCAL_API_TOKEN": lambda: secrets.token_urlsafe(32),
    "LOCAL_WORKSPACE_ID": lambda: str(uuid4()),
    "STORAGE_ADMIN_ACCESS_KEY": lambda: secrets.token_hex(16),
    "STORAGE_ADMIN_SECRET_KEY": lambda: secrets.token_hex(32),
    "STORAGE_ACCESS_KEY": lambda: secrets.token_hex(16),
    "STORAGE_SECRET_KEY": lambda: secrets.token_hex(32),
}


def build_local_env(template: str) -> str:
    lines = template.splitlines()
    for key, generate_value in GENERATED_VALUES.items():
        prefix = f"{key}="
        matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
        if len(matches) != 1 or lines[matches[0]] != prefix:
            raise ValueError(f".env.example must contain exactly one blank {key} entry")
        lines[matches[0]] = f"{prefix}{generate_value()}"

    return "\n".join(lines) + "\n"


def main() -> None:
    content = build_local_env(TEMPLATE_PATH.read_text(encoding="utf-8"))
    try:
        descriptor = os.open(OUTPUT_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env already exists; refusing to overwrite it.") from None

    with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
        env_file.write(content)

    print("Created .env with random local credentials and mode 0600.")


if __name__ == "__main__":
    main()
