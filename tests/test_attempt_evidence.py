import pytest
from sqlalchemy import select

from app.models import Attempt, AttemptRole, Quiz
from app.services.concept_scheduler_service import record_retrieval_feedback
from app.services.grading_service import get_mastery_rows, submit_attempt
from app.services.practice_service import PracticeAssemblyInput, assemble_practice_session
from app.services.question_health_service import (
    prepare_question_repair_context,
    record_attempt_evidence,
    score_question_health,
)
from app.services.teaching_brief_service import recent_misses
from tests.conftest import seed_test_quiz
from tests.workspace_clients import create_learner, learner_session, reviewer_session

pytestmark = pytest.mark.integration


def test_admin_test_attempt_stays_in_history_without_affecting_learner_evidence(db_session):
    quiz = db_session.scalar(select(Quiz))
    question = quiz.questions[0]
    wrong_choice = next(choice for choice in question.choices if not choice.is_correct)

    attempt = submit_attempt(db_session, quiz, {question.id: wrong_choice.id})

    assert attempt.actor_id == db_session.access.actor_id
    assert attempt.actor_role == AttemptRole.TEST
    assert attempt.submitted_at is not None
    assert get_mastery_rows(db_session)[0].total_attempts == 0
    assert recent_misses(db_session, question.subject_id) == []
    assert record_attempt_evidence(db_session, attempt.id) == []
    assert score_question_health(db_session, question.id).attempt_count == 0
    test_history = prepare_question_repair_context(
        db_session, question.id, actor_role=AttemptRole.TEST
    )["attempt_history"]
    assert test_history[0]["actor_id"] == db_session.access.actor_id
    assert test_history[0]["actor_name"] == "Parent"
    assert test_history[0]["actor_role"] == AttemptRole.TEST
    assert (
        prepare_question_repair_context(db_session, question.id, actor_role=AttemptRole.LEARNER)[
            "attempt_history"
        ]
        == []
    )
    with pytest.raises(ValueError, match="learner session"):
        record_retrieval_feedback(
            db_session, attempt_answer_id=attempt.answers[0].id, friction_rating=1
        )

    practice = assemble_practice_session(
        db_session, PracticeAssemblyInput(subject_id=question.subject_id, count=1)
    )
    assert practice.question_links[0].selection_score is not None
    assert practice.question_links[0].selection_score >= 90


def test_learner_attempt_counts_for_mastery_and_recent_misses(db_session):
    learner_id = create_learner("attempt-learner@example.com", "Learner")
    with learner_session(learner_id) as learner:
        seed_test_quiz(learner)
        quiz = learner.scalar(select(Quiz))
        question = quiz.questions[0]
        wrong_choice = next(choice for choice in question.choices if not choice.is_correct)

        attempt = submit_attempt(learner, quiz, {question.id: wrong_choice.id})

        assert attempt.actor_id == learner_id
        assert attempt.actor_role == AttemptRole.LEARNER
        assert get_mastery_rows(learner)[0].total_attempts == 1
        assert recent_misses(learner, question.subject_id)[0]["actor_id"] == learner_id
        assert recent_misses(learner, question.subject_id)[0]["actor_name"] == "Learner"
        assert score_question_health(learner, question.id).attempt_count == 1


def test_workspace_session_rejects_spoofed_or_changed_attribution(db_session):
    quiz = db_session.scalar(select(Quiz))
    db_session.add(
        Attempt(
            quiz_id=quiz.id,
            actor_id=db_session.access.actor_id,
            actor_role=AttemptRole.LEARNER,
        )
    )
    with pytest.raises(ValueError, match="Conflicting attribution"):
        db_session.commit()
    db_session.rollback()

    attempt = submit_attempt(db_session, quiz, {})
    attempt.actor_role = AttemptRole.UNKNOWN
    with pytest.raises(ValueError, match="Attribution is immutable"):
        db_session.commit()
    db_session.rollback()


def test_reviewer_preview_cannot_create_an_attempt(db_session):
    learner_id = create_learner("preview-learner@example.com", "Learner")
    with reviewer_session(learner_id) as reviewer:
        seed_test_quiz(reviewer)
        quiz = reviewer.scalar(select(Quiz))
        with pytest.raises(ValueError, match="Preview cannot record progress"):
            submit_attempt(reviewer, quiz, {})
