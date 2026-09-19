from __future__ import annotations

import time

from minio import Minio, MinioAdmin
from minio.credentials import StaticProvider
from minio.error import S3Error

from app.core.config import StorageAdminSettings

POLICY_NAME = "review-agent-document-access"


def main() -> None:
    settings = StorageAdminSettings()  # type: ignore[call-arg]
    client = Minio(
        settings.storage_endpoint,
        access_key=settings.storage_admin_access_key,
        secret_key=settings.storage_admin_secret_key.get_secret_value(),
        secure=settings.storage_secure,
    )
    admin = MinioAdmin(
        endpoint=settings.storage_endpoint,
        credentials=StaticProvider(
            settings.storage_admin_access_key,
            settings.storage_admin_secret_key.get_secret_value(),
        ),
        secure=settings.storage_secure,
    )

    for attempt in range(1, 31):
        try:
            if not client.bucket_exists(settings.storage_bucket):
                client.make_bucket(settings.storage_bucket)
            try:
                client.delete_bucket_policy(settings.storage_bucket)
            except S3Error as exc:
                if exc.code not in {"NoSuchBucketPolicy", "NoSuchPolicy"}:
                    raise
            admin.user_add(
                settings.storage_access_key,
                settings.storage_secret_key.get_secret_value(),
            )
            admin.policy_add(
                POLICY_NAME,
                policy={
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                            "Resource": [f"arn:aws:s3:::{settings.storage_bucket}"],
                        },
                        {
                            "Effect": "Allow",
                            "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                            "Resource": [f"arn:aws:s3:::{settings.storage_bucket}/*"],
                        },
                    ],
                },
            )
            admin.policy_set(POLICY_NAME, user=settings.storage_access_key)
            print("Private document bucket is ready.")
            return
        except Exception:
            if attempt == 30:
                raise
            time.sleep(1)


if __name__ == "__main__":
    main()
