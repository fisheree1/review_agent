from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from argon2.low_level import Type


class Argon2Passwords:
    def __init__(self) -> None:
        self.hasher = PasswordHasher(
            time_cost=2,
            memory_cost=19456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self.dummy_hash = self.hasher.hash("unavailable-account-placeholder")

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError):
            return False
