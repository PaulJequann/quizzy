import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attempt, AttemptAnswer, Choice, Question, Quiz

pytestmark = pytest.mark.api


@pytest.mark.parametrize(
    ("invalid_question", "detail"),
    [
        (True, "Unknown question in submission"),
        (False, "Selected choice does not belong to the question"),
    ],
)
def test_api_attempt_rejects_invalid_answers_without_recording_progress(
    client: TestClient, db_session: Session, invalid_question: bool, detail: str
) -> None:
    quiz = db_session.get(Quiz, 1)
    assert quiz is not None
    question = quiz.questions[0]
    other_question = Question(
        subject=question.subject,
        stem="An unrelated question",
        difficulty=question.difficulty,
        choices=[Choice(text="An unrelated choice", is_correct=True, order_index=1)],
    )
    db_session.add(other_question)
    db_session.commit()

    response = client.post(
        f"/api/quizzes/{quiz.id}/attempts",
        json={
            "answers": [
                {
                    "question_id": other_question.id if invalid_question else question.id,
                    "selected_choice_id": other_question.choices[0].id,
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert db_session.scalar(select(Attempt)) is None
    assert db_session.scalar(select(AttemptAnswer)) is None

    retry = client.post(
        f"/api/quizzes/{quiz.id}/attempts",
        json={
            "answers": [{"question_id": question.id, "selected_choice_id": question.choices[0].id}]
        },
    )
    assert retry.status_code == 201
    assert retry.json()["score"] == 100.0
