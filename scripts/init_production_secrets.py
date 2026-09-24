"""Create production secret files once, without printing their contents."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

GENERATED_NAMES = (
    "postgres_admin_password",
    "postgres_migrator_password",
    "postgres_runtime_password",
    "storage_admin_access_key",
    "storage_admin_secret_key",
    "storage_access_key",
    "storage_secret_key",
    "redis_password",
)
MODEL_NAMES = ("deepseek_api_key", "dashscope_api_key", "dashscope_embedding_url")
CONFIG_NAMES = ("redis_acl",)


def redis_acl_content(password: str) -> str:
    return (
        f"user default reset on >{password} ~limit:* +eval +exists +get +incr +expire +set +ping\n"
    )


def initialize(directory: Path, *, group_id: int) -> None:
    if os.geteuid() != 0:
        raise ValueError("Run this command as root on the production server")
    if not directory.is_absolute() or directory.is_symlink():
        raise ValueError("Use an absolute, non-symlink secret directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = directory.stat()
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o700:
        raise ValueError("Secret directory must be owned by root with mode 0700")
    names = (*GENERATED_NAMES, *MODEL_NAMES, *CONFIG_NAMES)
    if any((directory / name).exists() for name in names):
        raise ValueError("Secret files already exist; refusing to overwrite credentials")

    values = {name: secrets.token_urlsafe(36) for name in GENERATED_NAMES}
    for name in MODEL_NAMES:
        value = getpass.getpass(f"Enter {name}: ").strip()
        if len(value) < 8:
            raise ValueError(f"{name} must not be empty or a placeholder")
        values[name] = value
    endpoint = urlsplit(values["dashscope_embedding_url"])
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or not endpoint.hostname.endswith(".maas.aliyuncs.com")
    ):
        raise ValueError("dashscope_embedding_url must be the HTTPS Model Studio endpoint")
    values["redis_acl"] = redis_acl_content(values["redis_password"])

    created: list[Path] = []
    try:
        for name, value in values.items():
            path = directory / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.fchown(descriptor, 0, group_id)
                os.fchmod(descriptor, 0o640)
                output.write(value)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--group-id", type=int, default=10001)
    arguments = parser.parse_args()
    try:
        initialize(arguments.directory, group_id=arguments.group_id)
    except (OSError, ValueError) as exc:
        print(f"Secret initialization failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("Production secret files are ready; their values were not printed.")


if __name__ == "__main__":
    main()
