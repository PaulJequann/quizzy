import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import create_app
from app.models import Choice, Difficulty, Question, Quiz, QuizStatus


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        seed_test_quiz(session)
        yield session


@pytest.fixture
def client(db_session):
    app = create_app()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def seed_test_quiz(session: Session) -> None:
    quiz = Quiz(
        title="Test quiz",
        description="A small seeded quiz for route tests.",
        topic="testing",
        difficulty=Difficulty.BEGINNER,
        status=QuizStatus.PUBLISHED,
    )
    question = Question(stem="What does pytest run?", order_index=1)
    question.choices = [
        Choice(text="Tests", is_correct=True, order_index=1),
        Choice(text="Backups", is_correct=False, order_index=2),
        Choice(text="Migrations", is_correct=False, order_index=3),
        Choice(text="Containers", is_correct=False, order_index=4),
    ]
    quiz.questions.append(question)
    session.add(quiz)
    session.commit()
