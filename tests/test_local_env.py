from scripts.init_local_env import PASSWORD_KEYS, build_local_env
from scripts.upgrade_local_env import upgrade_local_env


def _values(content: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in content.splitlines() if line)


def test_new_local_env_has_distinct_non_empty_role_passwords() -> None:
    template = "\n".join(f"{key}=" for key in PASSWORD_KEYS) + "\n"

    values = _values(build_local_env(template))
    passwords = [values[key] for key in PASSWORD_KEYS]

    assert all(len(password) == 64 for password in passwords)
    assert len(set(passwords)) == len(passwords)


def test_legacy_local_env_upgrade_preserves_database_owner_credentials() -> None:
    legacy = (
        "POSTGRES_DB=review_agent\n"
        "POSTGRES_USER=existing_owner\n"
        "POSTGRES_PASSWORD=existing-secret\n"
    )

    values = _values(upgrade_local_env(legacy))

    assert values["POSTGRES_ADMIN_USER"] == "existing_owner"
    assert values["POSTGRES_ADMIN_PASSWORD"] == "existing-secret"
    assert values["POSTGRES_RUNTIME_USER"] == "review_agent_app"
    assert values["POSTGRES_MIGRATOR_USER"] == "review_agent_migrator"
    assert values["POSTGRES_RUNTIME_PASSWORD"] != values["POSTGRES_MIGRATOR_PASSWORD"]
    assert "POSTGRES_USER" not in values
    assert "POSTGRES_PASSWORD" not in values
