import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from alembic import command
from app.database import get_engine
from tests.db_utils import clear_database_caches, set_database_url

pytestmark = pytest.mark.migration


def test_test_database_is_migrated_to_alembic_head(test_database_url, monkeypatch) -> None:
    set_database_url(test_database_url, monkeypatch)
    head_revision = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()

    with get_engine().connect() as connection:
        current_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

    assert current_revision == head_revision


def test_migrations_create_question_first_schema(test_database_url, monkeypatch) -> None:
    set_database_url(test_database_url, monkeypatch)
    inspector = inspect(get_engine())

    assert {"question_generation_batches", "question_review_items"}.issubset(
        inspector.get_table_names()
    )
    assert {"generation_batch_id", "health_state", "source_excerpt"}.issubset(
        _column_names(inspector, "questions")
    )
    assert "session_type" in _column_names(inspector, "attempts")


def test_question_first_migration_backfills_attempt_session_type(
    test_database_url, monkeypatch
) -> None:
    set_database_url(test_database_url, monkeypatch)
    config = Config("alembic.ini")
    command.downgrade(config, "202606120005")
    try:
        _seed_pre_question_first_attempt()
        clear_database_caches()
        command.upgrade(config, "head")

        assert _attempt_session_type(900) == "generated"
    finally:
        clear_database_caches()
        command.upgrade(config, "head")
        clear_database_caches()


def _seed_pre_question_first_attempt() -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO subjects (id, name, slug, description, created_at, updated_at)
                VALUES (900, 'Migration', 'migration', '', now(), now())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO quizzes (
                    id,
                    title,
                    description,
                    difficulty,
                    status,
                    created_at,
                    updated_at,
                    primary_subject_id,
                    quiz_type
                )
                VALUES (
                    900,
                    'Migration quiz',
                    '',
                    'beginner',
                    'published',
                    now(),
                    now(),
                    900,
                    'generated'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO attempts (
                    id,
                    quiz_id,
                    score,
                    total_questions,
                    correct_count,
                    started_at,
                    submitted_at
                )
                VALUES (900, 900, 100, 1, 1, now(), now())
                """
            )
        )


def _attempt_session_type(attempt_id: int) -> str | None:
    with get_engine().connect() as connection:
        return connection.scalar(
            text("SELECT session_type FROM attempts WHERE id = :attempt_id"),
            {"attempt_id": attempt_id},
        )


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}
