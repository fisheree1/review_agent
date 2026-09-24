"""Validate a restic snapshot restored to an empty staging directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.backup_production import sha256_file
from scripts.init_production_secrets import CONFIG_NAMES, GENERATED_NAMES, MODEL_NAMES


def restored_path(root: Path, original: str) -> Path:
    if not original.startswith("/"):
        raise ValueError("Backup contains a non-absolute source path")
    candidate = (root / original.lstrip("/")).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Backup source path escapes the restored directory")
    return candidate


def verify(root: Path) -> dict[str, str]:
    manifests = [
        path
        for path in root.rglob("review-agent-backup-manifest.json")
        if path.parent.name.startswith("review-agent-backup-")
    ]
    if len(manifests) != 1:
        raise ValueError("Expected exactly one backup manifest")
    manifest_path = manifests[0]
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    if metadata.get("format") != 1:
        raise ValueError("Unsupported backup manifest format")
    revision = metadata.get("source_revision")
    if not isinstance(revision, str) or len(revision) != 40:
        raise ValueError("Backup has no source revision")
    database = manifest_path.with_name("database.dump")
    if not database.is_file() or sha256_file(database) != metadata.get("database_sha256"):
        raise ValueError("Database archive is missing or changed")
    mount = metadata.get("minio_mountpoint")
    if not isinstance(mount, str):
        raise ValueError("Backup has no MinIO mountpoint")
    minio = restored_path(root, mount)
    if not minio.is_dir() or not any(minio.iterdir()):
        raise ValueError("MinIO object store is missing or empty")
    secret_source = metadata.get("secrets_directory")
    if not isinstance(secret_source, str):
        raise ValueError("Backup has no recovery credentials path")
    secrets_dir = restored_path(root, secret_source)
    if not all(
        (secrets_dir / name).is_file() for name in (*GENERATED_NAMES, *MODEL_NAMES, *CONFIG_NAMES)
    ):
        raise ValueError("Recovery credentials are missing")
    return {
        "revision": revision,
        "database": str(database),
        "minio": str(minio),
        "secrets": str(secrets_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.root.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Restored backup validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Restored snapshot is complete for revision {result['revision']}.")
    print(f"Database archive: {result['database']}")
    print(f"MinIO data: {result['minio']}")
    print(f"Secret directory: {result['secrets']}")


if __name__ == "__main__":
    main()
