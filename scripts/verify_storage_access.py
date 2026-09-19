from __future__ import annotations

import os

from minio import Minio, MinioAdmin
from minio.credentials import StaticProvider
from minio.error import MinioAdminException

from app.core.config import StorageSettings


def main() -> None:
    forbidden_environment = {
        "STORAGE_ADMIN_ACCESS_KEY",
        "STORAGE_ADMIN_SECRET_KEY",
    }.intersection(os.environ)
    if forbidden_environment:
        raise RuntimeError("Application process received object-storage admin credentials")

    settings = StorageSettings()  # type: ignore[call-arg]
    secret_key = settings.storage_secret_key.get_secret_value()
    client = Minio(
        settings.storage_endpoint,
        access_key=settings.storage_access_key,
        secret_key=secret_key,
        secure=settings.storage_secure,
    )
    if not client.bucket_exists(settings.storage_bucket):
        raise RuntimeError("Application credentials cannot access the document bucket")

    admin = MinioAdmin(
        endpoint=settings.storage_endpoint,
        credentials=StaticProvider(settings.storage_access_key, secret_key),
        secure=settings.storage_secure,
    )
    try:
        admin.user_list()
    except MinioAdminException as exc:
        if "Status: 403" not in str(exc):
            raise
    else:
        raise RuntimeError("Application storage credentials unexpectedly have admin access")

    print("Application storage credentials are bucket-scoped and admin access is denied.")


if __name__ == "__main__":
    main()
