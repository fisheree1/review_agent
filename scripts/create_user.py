"""Bootstrap an account, optionally taking ownership of an existing local workspace."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from uuid import UUID

from app.auth.domain import normalize_email, validate_password
from app.auth.passwords import Argon2Passwords
from app.auth.store import SqlAuthStore
from app.core.config import get_settings
from app.core.database import async_session_factory, close_database


async def create_user(email: str, password: str, workspace_id: UUID | None) -> None:
    store = SqlAuthStore(async_session_factory)
    password_hash = await asyncio.to_thread(Argon2Passwords().hash, validate_password(password))
    user_id, assigned_workspace = await store.create_account(
        normalize_email(email), password_hash, workspace_id
    )
    print(f"Account {user_id} is ready in workspace {assigned_workspace}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--workspace-id", type=UUID, help="claim an existing unowned workspace")
    parser.add_argument(
        "--claim-local-workspace", action="store_true", help="claim LOCAL_WORKSPACE_ID"
    )
    args = parser.parse_args()
    if args.workspace_id and args.claim_local_workspace:
        parser.error("choose one workspace option")
    workspace_id = args.workspace_id
    if args.claim_local_workspace:
        workspace_id = get_settings().local_workspace_id
        if workspace_id is None:
            parser.error("LOCAL_WORKSPACE_ID is not set")
    password = getpass.getpass("New password (12–128 characters): ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        parser.error("passwords do not match")

    async def run() -> None:
        try:
            await create_user(args.email, password, workspace_id)
        finally:
            await close_database()

    asyncio.run(run())


if __name__ == "__main__":
    main()
