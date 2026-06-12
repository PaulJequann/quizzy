import os
from collections.abc import Iterator
from contextlib import contextmanager

from alembic.config import Config
from pytest import MonkeyPatch
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from alembic import command
from app.database import Base, get_engine, get_session_factory

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
    set_database_url(database_url, monkeypatch)
    engine = get_engine()
    truncate_database(engine)
    session_factory = get_session_factory()
    with session_factory() as session:
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
