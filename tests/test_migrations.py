import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.database import get_engine
from tests.db_utils import set_database_url

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


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}
