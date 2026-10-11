import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, select, text

from alembic import command
from app.database import Base, get_engine
from app.models import Difficulty, Quiz
from app.services.grading_service import submit_attempt
from app.services.question_health_service import QuestionIssueReport, report_question_issue
from app.services.settings_service import set_default_difficulty
from tests.db_utils import reset_schema

pytestmark = pytest.mark.migration

LEGACY_ORDER = (
    "subjects",
    "concepts",
    "quizzes",
    "questions",
    "choices",
    "quiz_questions",
    "question_concepts",
    "attempts",
    "attempt_answers",
    "question_review_items",
    "question_feedback_events",
    "app_settings",
)


def test_populated_legacy_upgrade_preserves_material_progress_feedback_and_settings(db_session):
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    report_question_issue(
        db_session,
        QuestionIssueReport(
            question_id=quiz.questions[0].id,
            category="unclear_wording",
            note="Original feedback",
            attempt_answer_id=attempt.answers[0].id,
            reported_from="detailed",
        ),
    )
    set_default_difficulty(db_session, Difficulty.ADVANCED)
    snapshot = _legacy_snapshot()
    db_session.close()
    config = Config("alembic.ini")
    reset_schema(config, "202607280001")
    try:
        _restore_legacy(snapshot)
        command.upgrade(config, "head")
        assert _legacy_snapshot() == snapshot
        with get_engine().connect() as connection:
            owner = connection.execute(
                text("SELECT id, is_active, is_superuser, hashed_password FROM users")
            ).one()
            assert owner.is_active is False
            assert owner.is_superuser is True
            assert owner.hashed_password.startswith("$argon2")
            for name in LEGACY_ORDER:
                assert set(connection.scalars(text(f"SELECT workspace_id FROM {name}"))) == {
                    owner.id
                }
    finally:
        command.upgrade(config, "head")


def _legacy_snapshot():
    with get_engine().connect() as connection:
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    select(
                        *(
                            column
                            for column in Base.metadata.tables[name].columns
                            if column.name != "workspace_id"
                        )
                    )
                ).mappings()
            ]
            for name in LEGACY_ORDER
        }


def _restore_legacy(snapshot):
    with get_engine().begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(LEGACY_ORDER) + " CASCADE"))
        metadata = MetaData()
        for name in LEGACY_ORDER:
            table = Table(name, metadata, autoload_with=connection)
            if snapshot[name]:
                connection.execute(table.insert(), snapshot[name])


def test_upgrade_reports_legacy_wrong_question_choice_without_losing_rows(db_session):
    quiz = db_session.scalar(select(Quiz))
    submit_attempt(db_session, quiz, {})
    snapshot = _legacy_snapshot()
    db_session.close()
    config = Config("alembic.ini")
    reset_schema(config, "202607280001")
    _restore_legacy(snapshot)
    with get_engine().begin() as connection:
        question = dict(snapshot["questions"][0], id=900, stem="Other legacy question")
        choice = dict(snapshot["choices"][0], id=900, question_id=900)
        metadata = MetaData()
        connection.execute(
            Table("questions", metadata, autoload_with=connection).insert(), question
        )
        connection.execute(Table("choices", metadata, autoload_with=connection).insert(), choice)
        connection.execute(text("UPDATE attempt_answers SET selected_choice_id=900"))
    try:
        with pytest.raises(RuntimeError, match="selected choices"):
            command.upgrade(config, "head")
        with get_engine().connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "202607280001"
            )
            assert connection.scalar(text("SELECT count(*) FROM attempt_answers")) == 1
            assert connection.scalar(text("SELECT selected_choice_id FROM attempt_answers")) == 900
    finally:
        with get_engine().begin() as connection:
            connection.execute(text("UPDATE attempt_answers SET selected_choice_id=NULL"))
        command.upgrade(config, "head")
