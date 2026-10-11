import os
from collections.abc import Iterator
from contextlib import contextmanager

from alembic.config import Config
from fastapi_users.password import PasswordHelper
from pytest import MonkeyPatch
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from alembic import command
from app.auth.models import User
from app.database import Base, get_engine, get_session_factory
from app.study_access import resolve_study_access
from app.workspace_database import workspace_session

POSTGRES_IMAGE = "postgres:16-alpine"
POSTGRES_USER = "quizzy"
POSTGRES_PASSWORD = "quizzy"
POSTGRES_DB = "quizzy_test"


@contextmanager
def testcontainers_postgres_database() -> Iterator[str]:
    with PostgresContainer(
        POSTGRES_IMAGE,
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
        driver="psycopg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        migrate_database(database_url)
        yield database_url


def migrate_database(database_url: str) -> None:
    set_database_url(database_url)
    command.upgrade(Config("alembic.ini"), "head")
    clear_database_caches()


def reset_database(database_url: str, monkeypatch: MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    set_database_url(database_url, monkeypatch)
    engine = get_engine()
    truncate_database(engine)
    session_factory = get_session_factory()
    with session_factory() as accounts:
        parent = User(
            email="parent@example.com",
            hashed_password=PasswordHelper().hash("test-password"),
            display_name="Parent",
            is_active=True,
            is_superuser=True,
        )
        accounts.add(parent)
        accounts.commit()
        access = resolve_study_access(accounts, parent)
    monkeypatch.setenv("MCP_ADMIN_USER_ID", str(access.actor_id))
    from app.config import get_settings

    get_settings.cache_clear()
    with workspace_session(access) as session:
        yield session


def set_database_url(database_url: str, monkeypatch: MonkeyPatch | None = None) -> None:
    if not database_url.startswith("postgresql"):
        raise RuntimeError("Tests must run against Testcontainers Postgres.")
    if monkeypatch is None:
        os.environ["DATABASE_URL"] = database_url
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()


def truncate_database(engine: Engine) -> None:
    table_names = _app_table_names(engine)
    if not table_names:
        return
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE"))


def clear_database_caches() -> None:
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    from app.config import get_settings

    get_settings.cache_clear()


def _app_table_names(engine: Engine) -> list[str]:
    preparer = engine.dialect.identifier_preparer
    return [preparer.format_table(table) for table in Base.metadata.sorted_tables]


def get_test_session_factory():
    from app.study_access import StudyAccess
    from app.workspace_database import WorkspaceSession

    return lambda: WorkspaceSession(
        StudyAccess(1, 1, True, "Parent"),
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


def reset_schema(config, revision):
    with get_engine().begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(config, revision)
