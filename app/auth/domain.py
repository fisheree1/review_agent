from __future__ import annotations

from app.core.errors import ApplicationError


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if (
        len(email) > 254
        or not email.isascii()
        or any(character.isspace() for character in email)
        or email.count("@") != 1
    ):
        raise ApplicationError(code="EMAIL_INVALID", message="请输入有效邮箱地址", status_code=422)
    local, domain = email.split("@")
    labels = domain.split(".")
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or any(
            not label or len(label) > 63 or label[0] == "-" or label[-1] == "-" for label in labels
        )
        or any(
            not all(character.isalnum() or character == "-" for character in label)
            for label in labels
        )
    ):
        raise ApplicationError(code="EMAIL_INVALID", message="请输入有效邮箱地址", status_code=422)
    return email


def validate_password(value: str) -> str:
    if not 12 <= len(value) <= 128:
        raise ApplicationError(
            code="PASSWORD_INVALID", message="密码需为 12–128 个字符", status_code=422
        )
    return value
