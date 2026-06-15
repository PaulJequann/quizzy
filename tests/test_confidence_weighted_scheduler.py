import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AttemptAnswer,
    Concept,
    ConceptReviewState,
    Question,
    QuestionHealthState,
    QuestionReviewItem,
)
from app.services.grading_service import get_mastery_rows

pytestmark = pytest.mark.api


def test_retrieval_feedback_persists_and_updates_concept_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    question = _seeded_question(db_session)
    correct_choice = next(choice for choice in question.choices if choice.is_correct)
    result_response = client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(correct_choice.id)},
    )
    assert result_response.status_code == 200
    assert "Retrieval feel" in result_response.text
    assert "Report issue" in result_response.text
    answer = _answer_for_question(db_session, question)

    response = client.post(
        f"/api/attempt-answers/{answer.id}/retrieval-feedback",
        json={"retrieval_friction_rating": 3, "response_time_ms": 1200},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updates"][0]["spacing_eligible"] is True
    assert "fluent_correct_interval_advanced" in payload["updates"][0]["reason_codes"]
    db_session.expire_all()
    updated_answer = db_session.get(AttemptAnswer, answer.id)
    assert updated_answer is not None
    assert updated_answer.retrieval_friction_rating == 3
    assert updated_answer.response_time_ms == 1200
    state = db_session.scalar(select(ConceptReviewState))
    assert state is not None
    assert state.interval_days == 3
    assert state.consecutive_spaced_correct == 1
    assert state.due_at is not None


def test_results_page_marks_recorded_retrieval_feedback_as_selected(
    client: TestClient,
    db_session: Session,
) -> None:
    answer = _submit_seeded_answer(client, db_session, correct=True)
    response = _post_feedback(client, answer, rating=3)

    results_response = client.get(f"/attempts/{answer.attempt_id}/results")

    assert response.status_code == 200
    assert results_response.status_code == 200
    assert "Recorded: <span data-feedback-label>Fluent</span>" in results_response.text
    assert 'data-friction-button="3"' in results_response.text
    assert 'aria-pressed="true"' in results_response.text
    assert "✓ </span>3 Fluent" in results_response.text


def test_fluent_retrieval_feedback_is_rejected_for_wrong_answers(
    client: TestClient,
    db_session: Session,
) -> None:
    question = _seeded_question(db_session)
    wrong_choice = next(choice for choice in question.choices if not choice.is_correct)
    client.post("/quizzes/1/submit", data={f"question_{question.id}": str(wrong_choice.id)})
    answer = _answer_for_question(db_session, question)

    response = client.post(
        f"/api/attempt-answers/{answer.id}/retrieval-feedback",
        json={"retrieval_friction_rating": 3},
    )

    assert response.status_code == 422
    db_session.expire_all()
    unchanged_answer = db_session.get(AttemptAnswer, answer.id)
    assert unchanged_answer is not None
    assert unchanged_answer.retrieval_friction_rating is None


def test_question_issue_report_creates_review_work_without_rewriting_answer(
    client: TestClient,
    db_session: Session,
) -> None:
    question = _seeded_question(db_session)
    correct_choice = next(choice for choice in question.choices if choice.is_correct)
    client.post("/quizzes/1/submit", data={f"question_{question.id}": str(correct_choice.id)})
    answer = _answer_for_question(db_session, question)
    original_correctness = answer.is_correct

    first_response = client.post(
        f"/api/questions/{question.id}/report-issue",
        json={
            "category": "bad_distractors",
            "note": "Two choices are too easy to discard.",
            "attempt_answer_id": answer.id,
        },
    )
    second_response = client.post(
        f"/api/questions/{question.id}/report-issue",
        json={
            "category": "bad_distractors",
            "note": "Distractors still need revision.",
            "attempt_answer_id": answer.id,
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["review_item"]["id"] == second_response.json()["review_item"]["id"]
    assert second_response.json()["health_state"] == QuestionHealthState.NEEDS_REVIEW.value
    review_items = db_session.scalars(select(QuestionReviewItem)).all()
    assert len(review_items) == 1
    assert review_items[0].suggested_action == "improve_distractors"
    db_session.expire_all()
    unchanged_answer = db_session.get(AttemptAnswer, answer.id)
    assert unchanged_answer is not None
    assert unchanged_answer.is_correct is original_correctness


def test_wrong_shaky_feedback_resets_concept_interval(
    client: TestClient,
    db_session: Session,
) -> None:
    answer = _submit_seeded_answer(client, db_session, correct=False)

    response = _post_feedback(client, answer, rating=1)

    assert response.status_code == 200
    payload = response.json()
    assert "retrieval_lapse" in payload["updates"][0]["reason_codes"]
    db_session.expire_all()
    state = _review_state(db_session)
    assert state.interval_days == 1
    assert state.lapse_count == 1
    assert state.consecutive_spaced_correct == 0


def test_fluent_repeat_in_same_session_does_not_advance_interval_twice(
    client: TestClient,
    db_session: Session,
) -> None:
    answer = _submit_seeded_answer(client, db_session, correct=True)
    first_response = _post_feedback(client, answer, rating=3)

    second_response = _post_feedback(client, answer, rating=3)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    update = second_response.json()["updates"][0]
    assert update["interval_days"] == 3
    assert "same_session_repeat_not_advanced" in update["reason_codes"]
    db_session.expire_all()
    state = _review_state(db_session)
    assert state.consecutive_spaced_correct == 1


def test_suspect_question_does_not_advance_concept_interval(
    client: TestClient,
    db_session: Session,
) -> None:
    answer = _submit_seeded_answer(client, db_session, correct=True)
    question = answer.question
    question.health_state = QuestionHealthState.SUSPECT
    db_session.commit()

    response = _post_feedback(client, answer, rating=3)

    assert response.status_code == 200
    update = response.json()["updates"][0]
    assert update["interval_days"] == 1
    assert "suspect_question_evidence_ignored" in update["reason_codes"]
    db_session.expire_all()
    state = _review_state(db_session)
    assert state.consecutive_spaced_correct == 0
    assert state.due_at is None


def test_repeated_exact_question_evidence_is_capped_in_mastery(
    client: TestClient,
    db_session: Session,
) -> None:
    question = _seeded_question(db_session)
    correct_choice = next(choice for choice in question.choices if choice.is_correct)
    for _ in range(5):
        client.post("/quizzes/1/submit", data={f"question_{question.id}": str(correct_choice.id)})

    concept = _seeded_concept(db_session)
    row = next(row for row in get_mastery_rows(db_session) if row.concept.id == concept.id)

    assert row.accuracy == 100
    assert row.conservative_performance < 70
    assert "low_confidence" in row.reason_codes


def _seeded_question(db_session: Session) -> Question:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    return question


def _seeded_concept(db_session: Session) -> Concept:
    concept = db_session.scalar(select(Concept).where(Concept.name == "pytest basics"))
    assert concept is not None
    return concept


def _answer_for_question(db_session: Session, question: Question) -> AttemptAnswer:
    answer = db_session.scalar(
        select(AttemptAnswer).where(AttemptAnswer.question_id == question.id)
    )
    assert answer is not None
    return answer


def _submit_seeded_answer(
    client: TestClient,
    db_session: Session,
    *,
    correct: bool,
) -> AttemptAnswer:
    question = _seeded_question(db_session)
    choice = next(choice for choice in question.choices if choice.is_correct is correct)
    client.post("/quizzes/1/submit", data={f"question_{question.id}": str(choice.id)})
    return _answer_for_question(db_session, question)


def _post_feedback(client: TestClient, answer: AttemptAnswer, *, rating: int):
    return client.post(
        f"/api/attempt-answers/{answer.id}/retrieval-feedback",
        json={"retrieval_friction_rating": rating},
    )


def _review_state(db_session: Session) -> ConceptReviewState:
    state = db_session.scalar(select(ConceptReviewState))
    assert state is not None
    return state
