import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Difficulty, Question, QuestionStatus, Quiz

pytestmark = pytest.mark.api


def test_subject_pages_show_namespace_workspace(client: TestClient) -> None:
    list_response = client.get("/subjects")

    assert list_response.status_code == 200
    assert "Subjects" in list_response.text
    assert "testing" in list_response.text
    assert "1 concepts" in list_response.text
    assert "1 total questions" in list_response.text

    detail_response = client.get("/subjects/1")
    assert detail_response.status_code == 200
    assert "Overall Mastery" in detail_response.text
    assert "Study options" in detail_response.text
    assert "Start quick practice" in detail_response.text
    assert "Save concept" in detail_response.text
    assert "Pytest Basics" in detail_response.text
    assert "What does pytest run?" in detail_response.text
    assert "Test quiz" in detail_response.text


def test_subject_question_counts_distinguish_active_and_draft(
    client: TestClient,
    db_session: Session,
) -> None:
    _add_draft_question(db_session)

    list_response = client.get("/subjects")
    detail_response = client.get("/subjects/1")

    assert list_response.status_code == 200
    assert "2 total questions" in list_response.text
    assert "1 active" in list_response.text
    assert "1 draft" in list_response.text
    assert detail_response.status_code == 200
    assert "Draft taxonomy question?" in detail_response.text
    assert "2 total" in detail_response.text
    assert "1 active" in detail_response.text
    assert "1 draft" in detail_response.text


def _add_draft_question(db_session: Session) -> None:
    quiz = db_session.get(Quiz, 1)
    assert quiz is not None
    assert quiz.primary_subject is not None
    db_session.add(
        Question(
            subject=quiz.primary_subject,
            stem="Draft taxonomy question?",
            status=QuestionStatus.DRAFT,
            difficulty=Difficulty.ADVANCED,
            source_reference="testing/draft.md",
        )
    )
    db_session.commit()
