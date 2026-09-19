from alembic import command
from alembic.config import Config

from scripts.provision_database_roles import main as provision_database_roles


def main() -> None:
    provision_database_roles()
    command.upgrade(Config("alembic.ini"), "head")
    print("Database migration is at head.")


if __name__ == "__main__":
    main()
