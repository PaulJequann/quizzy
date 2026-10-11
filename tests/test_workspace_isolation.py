import pytest
from sqlalchemy import Table, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload

from app.auth.models import User
from app.database import get_engine, get_session_factory
from app.models import (
    Choice,
    Question,
    Quiz,
    SourceDocument,
    Subject,
    WorkspaceOwned,
)
from app.study_access import resolve_study_access
from app.workspace_database import workspace_session

pytestmark = pytest.mark.integration


def learner_access():
    with get_session_factory()() as accounts:
        user = User(
            email="isolated@example.com",
            hashed_password="!",
            display_name="Isolated",
            is_active=True,
            is_superuser=False,
        )
        accounts.add(user)
        accounts.commit()
        return resolve_study_access(accounts, user)


def test_entities_columns_aliases_relationships_and_refresh_are_scoped(db_session):
    access = learner_access()
    with workspace_session(access) as child:
        subject = Subject(name="Child subject", slug="testing")
        quiz = Quiz(title="Child quiz", primary_subject=subject)
        child.add(quiz)
        child.commit()
        child_id = quiz.id
    with workspace_session(db_session.access) as parent:
        assert parent.get(Quiz, child_id) is None
        assert parent.scalar(select(func.count(Quiz.id))) == 1
        subject_alias = aliased(Subject)
        assert parent.scalar(select(subject_alias.name)) == "testing"
        quiz = parent.scalar(select(Quiz).options(selectinload(Quiz.question_links)))
        assert quiz.primary_subject.name == "testing"
        assert quiz.questions[0].stem == "What does pytest run?"
        parent.refresh(quiz)
        assert quiz.workspace_id == 1
    with workspace_session(access) as child:
        assert list(child.scalars(select(Subject.name))) == ["Child subject"]
        assert child.scalar(select(func.count(Question.id))) == 0


def test_database_rejects_cross_workspace_associations(db_session):
    access = learner_access()
    with (
        get_engine().begin() as connection,
        pytest.raises(IntegrityError),
        connection.begin_nested(),
    ):
        connection.execute(
            text(
                "INSERT INTO quiz_questions "
                "(workspace_id, quiz_id, question_id, order_index, selection_reason) "
                "VALUES (:owner, 1, 1, 0, :reason)"
            ),
            {"owner": access.workspace_id, "reason": ""},
        )


def test_database_rejects_wrong_question_choice(db_session):
    from app.services.grading_service import submit_attempt

    question = Question(stem="Other", subject_id=1)
    question.choices.append(Choice(text="Not an answer to the first question"))
    db_session.add(question)
    db_session.commit()
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    answer_id = attempt.answers[0].id
    with (
        get_engine().begin() as connection,
        pytest.raises(IntegrityError),
        connection.begin_nested(),
    ):
        connection.execute(
            text("UPDATE attempt_answers SET selected_choice_id=:choice WHERE id=:answer"),
            {"choice": question.choices[0].id, "answer": answer_id},
        )


def test_nullable_delete_keeps_workspace(db_session):
    subject = Subject(name="Disposable", slug="disposable")
    db_session.add(subject)
    db_session.commit()
    source = SourceDocument(
        subject_id=subject.id,
        title="Source",
        source_key="same.md",
        content="Text",
        current_content_sha256="a" * 64,
    )
    db_session.add(source)
    db_session.commit()
    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM subjects WHERE id=:id"), {"id": subject.id})
    db_session.refresh(source)
    assert source.subject_id is None
    assert source.workspace_id == 1


def test_source_identity_and_settings_are_private(db_session):
    from app.models import Difficulty
    from app.services.settings_service import get_default_difficulty, set_default_difficulty

    access = learner_access()
    set_default_difficulty(db_session, Difficulty.ADVANCED)
    for selected in (db_session.access, access):
        with workspace_session(selected) as private:
            private.add(
                SourceDocument(
                    title="Private",
                    source_key="same.md",
                    content="Text",
                    current_content_sha256="a" * 64,
                )
            )
            private.commit()
    with workspace_session(access) as child:
        set_default_difficulty(child, Difficulty.BEGINNER)
        assert get_default_difficulty(child) == Difficulty.BEGINNER
    assert get_default_difficulty(db_session) == Difficulty.ADVANCED


def test_every_private_model_and_reference_has_database_ownership_constraint(db_session):
    from app.database import Base

    inspector = inspect(get_engine())
    for mapper in Base.registry.mappers:
        if not issubclass(mapper.class_, WorkspaceOwned):
            continue
        table = mapper.local_table
        assert isinstance(table, Table)
        constraints = inspector.get_foreign_keys(table.name)
        assert any(
            fk["referred_table"] == "users" and fk["constrained_columns"] == ["workspace_id"]
            for fk in constraints
        ), table.name
        _assert_reference_ownership(table, constraints)


def _assert_reference_ownership(table, constraints):
    for reference in table.foreign_keys:
        if reference.column.table.name == "users":
            continue
        assert any(
            fk["constrained_columns"] == ["workspace_id", reference.parent.name]
            and fk["referred_columns"] == ["workspace_id", "id"]
            and fk["referred_table"] == reference.column.table.name
            for fk in constraints
        ), (table.name, reference.parent.name)


def test_uncorrelated_subquery_is_private(db_session):
    from app.models import Quiz

    access = learner_access()
    with workspace_session(access) as child:
        count = select(func.count(Quiz.id)).scalar_subquery()
        assert child.scalar(select(count)) == 0


def test_autogenerate_preserves_migration_owned_constraints(db_session):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database import Base
    from app.migration_policy import include_schema_object

    with get_engine().connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"include_object": include_schema_object}
        )
        diffs = compare_metadata(context, Base.metadata)
    removed = [item[1].name for item in diffs if item[0] in {"remove_fk", "remove_constraint"}]
    assert not any(name and name.endswith("_workspace") for name in removed)
    assert "fk_answer_choice_question" not in removed
