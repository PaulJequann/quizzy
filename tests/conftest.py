import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import create_app
from app.models import (
    Choice,
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionStatus,
    Quiz,
    QuizQuestion,
    QuizStatus,
    Subject,
)
from app.services.concept_service import normalize_concept_name
from tests.db_utils import clear_database_caches, reset_database, testcontainers_postgres_database


@pytest.fixture(scope="session")
def test_database_url():
    with testcontainers_postgres_database() as database_url:
        yield database_url
    clear_database_caches()


@pytest.fixture
def db_session(test_database_url, monkeypatch):
    for session in reset_database(test_database_url, monkeypatch):
        seed_test_quiz(session)
        yield session
    clear_database_caches()


@pytest.fixture
def client(db_session):
    app = create_app()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    browser = TestClient(app, headers={"origin": "http://testserver"})
    response = browser.post(
        "/auth/login", data={"username": "parent@example.com", "password": "test-password"}
    )
    assert response.status_code == 204
    return browser


def seed_test_quiz(session: Session) -> None:
    subject = Subject(name="testing", slug="testing")
    quiz = Quiz(
        title="Test quiz",
        description="A small seeded quiz for route tests.",
        primary_subject=subject,
        difficulty=Difficulty.BEGINNER,
        status=QuizStatus.PUBLISHED,
    )
    question = Question(
        subject=subject,
        stem="What does pytest run?",
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        source_reference="testing/pytest.md#basics",
    )
    question.choices = [
        Choice(text="Tests", is_correct=True, order_index=1),
        Choice(text="Backups", is_correct=False, order_index=2),
        Choice(text="Migrations", is_correct=False, order_index=3),
        Choice(text="Containers", is_correct=False, order_index=4),
    ]
    question.concept_links = [
        QuestionConcept(
            concept=Concept(
                name="pytest basics",
                slug="pytest-basics",
                normalized_name=normalize_concept_name("pytest basics", subject_name="testing"),
                status=ConceptStatus.ACTIVE,
                subject=subject,
            ),
            role=ConceptAssignmentRole.PRIMARY,
        )
    ]
    quiz.question_links.append(QuizQuestion(question=question, order_index=1))
    session.add(quiz)
    session.commit()
