from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import select, text

from alembic import command
from app.database import get_engine
from app.models import Concept, ConceptReviewState, Question, QuestionStatus, Quiz
from app.services.grading_service import submit_attempt
from app.services.question_health_service import QuestionIssueReport, report_question_issue

pytestmark = pytest.mark.migration


def test_upgrade_preserves_history_and_resets_derived_state(db_session):
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    concept = db_session.scalar(select(Concept))
    question = db_session.scalar(select(Question))
    db_session.add(ConceptReviewState(concept_id=concept.id, interval_days=8, lapse_count=3))
    flagged = Question(subject_id=question.subject_id, stem="Flagged")
    archived = Question(
        subject_id=question.subject_id, stem="Archived", status=QuestionStatus.ARCHIVED
    )
    db_session.add_all([flagged, archived])
    db_session.commit()
    report_question_issue(
        db_session,
        QuestionIssueReport(
            question_id=flagged.id,
            category="incorrect_answer",
            note="Wrong",
            reported_from="detailed",
        ),
    )
    flagged_id, archived_id = flagged.id, archived.id
    question.health_state = "healthy"
    question.health_score = 85
    db_session.commit()
    attempt_id, quiz_id, workspace_id, question_id = (
        attempt.id,
        quiz.id,
        attempt.workspace_id,
        question.id,
    )
    db_session.close()
    config = Config("alembic.ini")
    command.downgrade(config, "202610170001")
    try:
        with get_engine().begin() as connection:
            assignment_id = connection.scalar(
                text(
                    "INSERT INTO exam_assignments "
                    "(workspace_id, assigned_by_id, source_quiz_id, title_snapshot, paper_json, "
                    "assigned_at, request_key) VALUES (:owner, :owner, :quiz, 'Exam', "
                    "'[{}]'::jsonb, now(), :key) RETURNING id"
                ),
                {"owner": workspace_id, "quiz": quiz_id, "key": uuid4()},
            )
            submission_id = connection.scalar(
                text(
                    "INSERT INTO exam_submissions "
                    "(workspace_id, assignment_id, responses_json, submitted_at) "
                    "VALUES (:owner, :assignment, '{}'::jsonb, now()) RETURNING id"
                ),
                {"owner": workspace_id, "assignment": assignment_id},
            )
            before = (
                connection.execute(text("SELECT * FROM attempts WHERE id=:id"), {"id": attempt_id})
                .mappings()
                .one()
            )
            before = dict(before)
        command.upgrade(config, "head")
        with get_engine().connect() as connection:
            after = dict(
                connection.execute(text("SELECT * FROM attempts WHERE id=:id"), {"id": attempt_id})
                .mappings()
                .one()
            )
            assert after.pop("actor_id") is None
            assert after.pop("actor_role") == "unknown"
            assert after == before
            assert connection.execute(
                text("SELECT actor_id, actor_role FROM exam_submissions WHERE id=:id"),
                {"id": submission_id},
            ).one() == (None, "unknown")
            assert connection.execute(
                text(
                    "SELECT interval_days, ease_factor, due_at, last_review_at, "
                    "successful_spaced_reviews, consecutive_spaced_correct, lapse_count, "
                    "last_friction_rating FROM concept_review_states"
                )
            ).one() == (1, 2.0, None, None, 0, 0, 0, None)
            assert connection.execute(
                text(
                    "SELECT health_state, health_score, last_health_checked_at "
                    "FROM questions WHERE id=:id"
                ),
                {"id": question_id},
            ).one() == ("untested", None, None)
            states = dict(
                connection.execute(
                    text(
                        "SELECT id, health_state FROM questions WHERE id IN (:flagged, :archived)"
                    ),
                    {"flagged": flagged_id, "archived": archived_id},
                ).all()
            )
            assert states == {flagged_id: "needs_review", archived_id: "archived"}
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM attempt_answers WHERE attempt_id=:id"),
                    {"id": attempt_id},
                )
                > 0
            )
    finally:
        command.upgrade(config, "head")
