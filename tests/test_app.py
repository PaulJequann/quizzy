from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Choice, Concept, Question


def test_app_starts(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Quizzy" in response.text


def test_quiz_list_route_returns_200(client: TestClient) -> None:
    response = client.get("/quizzes")
    assert response.status_code == 200
    assert "Test quiz" in response.text


def test_question_creation_tags_concepts(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/quizzes/1/questions",
        data={
            "stem": "What does a fixture provide?",
            "choice_1": "Reusable test setup",
            "choice_2": "A production database",
            "choice_3": "A deployment target",
            "choice_4": "A package lockfile",
            "correct_choice": "0",
            "explanation": "Fixtures provide reusable setup for tests.",
            "source_reference": "testing/pytest.md#fixtures",
            "concepts": "pytest basics, fixtures, Fixtures",
        },
    )

    assert response.status_code == 200
    question = db_session.scalar(
        select(Question).where(Question.stem == "What does a fixture provide?")
    )
    assert question is not None
    assert [concept.name for concept in question.concepts] == ["fixtures", "pytest basics"]
    concepts = db_session.scalars(select(Concept).order_by(Concept.name)).all()
    assert [concept.name for concept in concepts] == ["fixtures", "pytest basics"]


def test_mastery_page_tracks_attempt_accuracy(client: TestClient, db_session: Session) -> None:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    wrong_choice = db_session.scalar(
        select(Choice).where(Choice.question_id == question.id, Choice.is_correct.is_(False))
    )
    assert wrong_choice is not None

    result_response = client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(wrong_choice.id)},
    )

    assert result_response.status_code == 200
    assert "Concepts to review" in result_response.text
    assert "pytest basics" in result_response.text

    mastery_response = client.get("/mastery")
    assert mastery_response.status_code == 200
    assert "pytest basics" in mastery_response.text
    assert "0%" in mastery_response.text
    assert "weak" in mastery_response.text
