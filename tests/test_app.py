import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AttemptAnswer,
    Choice,
    Concept,
    Question,
    QuestionHealthState,
    QuestionStatus,
    Quiz,
    QuizQuestion,
    QuizType,
    Subject,
)
from app.routes import web as web_routes

pytestmark = pytest.mark.api


def test_app_starts(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Quizzy" in response.text


def test_quiz_list_route_returns_200(client: TestClient) -> None:
    response = client.get("/quizzes")
    assert response.status_code == 200
    assert "Test quiz" in response.text


def test_take_page_shuffles_presentation_without_position_based_grading(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiz = db_session.get(Quiz, 1)
    assert quiz is not None
    assert quiz.primary_subject is not None
    second_question = Question(
        subject=quiz.primary_subject,
        stem="Which command runs the test suite?",
        status=QuestionStatus.ACTIVE,
        difficulty=quiz.difficulty,
        source_reference="testing/pytest.md#commands",
    )
    second_question.choices = [
        Choice(text="uv run pytest", is_correct=True, order_index=1),
        Choice(text="uv run alembic upgrade head", is_correct=False, order_index=2),
        Choice(text="uv run uvicorn app.main:app", is_correct=False, order_index=3),
        Choice(text="uv run ruff format .", is_correct=False, order_index=4),
    ]
    quiz.question_links.append(QuizQuestion(question=second_question, order_index=2))
    db_session.commit()

    monkeypatch.setattr(
        web_routes.random, "sample", lambda population, k: list(reversed(population))
    )
    response = client.get("/quizzes/1/take")

    assert response.status_code == 200
    assert response.text.index("Which command runs the test suite?") < response.text.index(
        "What does pytest run?"
    )
    assert response.text.index("Containers") < response.text.index("Tests")

    correct_answers = {
        question.id: next(choice.id for choice in question.choices if choice.is_correct)
        for question in quiz.questions
    }
    result_response = client.post(
        "/quizzes/1/submit",
        data={
            f"question_{question_id}": str(choice_id)
            for question_id, choice_id in correct_answers.items()
        },
    )

    assert result_response.status_code == 200
    assert "100%" in result_response.text


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
    assert question.subject.slug == "testing"
    assert [concept.name for concept in question.concepts] == ["fixtures", "pytest basics"]
    concepts = db_session.scalars(select(Concept).order_by(Concept.name)).all()
    assert [concept.name for concept in concepts] == ["fixtures", "pytest basics"]
    assert [concept.subject.slug for concept in concepts] == ["testing", "testing"]


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
    assert "Pytest Basics" in result_response.text
    answer = db_session.scalar(
        select(AttemptAnswer).where(AttemptAnswer.question_id == question.id)
    )
    assert answer is not None
    assert answer.question_stem_snapshot == "What does pytest run?"
    assert answer.selected_choice_text_snapshot == wrong_choice.text
    assert answer.correct_choice_text_snapshot == "Tests"

    mastery_response = client.get("/mastery")
    assert mastery_response.status_code == 200
    assert "data-mastery-search" in mastery_response.text
    assert 'data-status-filter="weak"' in mastery_response.text
    assert "data-mastery-row" in mastery_response.text
    assert "Pytest Basics" in mastery_response.text
    assert "0%" in mastery_response.text
    assert "Weak" in mastery_response.text


def test_results_page_falls_back_to_live_answer_and_shows_question_metadata(
    client: TestClient, db_session: Session
) -> None:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    correct_choice = db_session.scalar(
        select(Choice).where(Choice.question_id == question.id, Choice.is_correct.is_(True))
    )
    assert correct_choice is not None

    client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(correct_choice.id)},
    )
    answer = db_session.scalar(
        select(AttemptAnswer).where(AttemptAnswer.question_id == question.id)
    )
    assert answer is not None
    answer.question_stem_snapshot = ""
    answer.selected_choice_text_snapshot = ""
    db_session.commit()

    response = client.get(f"/attempts/{answer.attempt_id}/results")

    assert response.status_code == 200
    assert "No answer" not in response.text
    assert "Tests" in response.text
    assert "testing" in response.text
    assert ">Beginner<" in response.text
    assert ">Active<" in response.text
    assert ">Source<" in response.text
    assert "Source: testing/pytest.md#basics" not in response.text
    assert "testing/pytest.md#basics" in response.text
    assert "Pytest Basics" in response.text


def test_weak_concepts_api_returns_queue_evidence(client: TestClient, db_session: Session) -> None:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    wrong_choice = db_session.scalar(
        select(Choice).where(Choice.question_id == question.id, Choice.is_correct.is_(False))
    )
    assert wrong_choice is not None
    client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(wrong_choice.id)},
    )

    response = client.get("/api/mastery/weak-concepts")

    assert response.status_code == 200
    assert response.json() == [
        {
            "concept_id": 1,
            "subject_id": 1,
            "subject_name": "testing",
            "name": "pytest basics",
            "total_attempts": 1,
            "correct_attempts": 0,
            "accuracy": 0.0,
            "last_missed_at": response.json()[0]["last_missed_at"],
            "status": "weak",
            "missed_question_ids": [question.id],
            "source_references": ["testing/pytest.md#basics"],
        }
    ]


def test_review_weak_concepts_reuses_existing_questions(
    client: TestClient, db_session: Session
) -> None:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    wrong_choice = db_session.scalar(
        select(Choice).where(Choice.question_id == question.id, Choice.is_correct.is_(False))
    )
    assert wrong_choice is not None
    client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(wrong_choice.id)},
    )

    response = client.post("/mastery/review", follow_redirects=False)

    review_quiz = db_session.scalar(
        select(Quiz)
        .where(Quiz.quiz_type == QuizType.PRACTICE, Quiz.assembly_strategy == "mastery_review")
        .options(selectinload(Quiz.questions))
    )
    assert review_quiz is not None
    assert response.status_code == 303
    assert response.headers["location"] == f"/quizzes/{review_quiz.id}/take"
    assert [review_question.id for review_question in review_quiz.questions] == [question.id]
    assert (
        db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
        is question
    )
    links = db_session.scalars(
        select(QuizQuestion).where(QuizQuestion.question_id == question.id)
    ).all()
    assert sorted(link.quiz_id for link in links) == [1, review_quiz.id]
    assert review_quiz.quiz_type == QuizType.PRACTICE
    assert review_quiz.is_disposable is True
    assert review_quiz.assembly_strategy == "mastery_review"


def test_subjects_namespace_concepts(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/quizzes",
        data={
            "title": "Another subject",
            "description": "",
            "topic": "AWS",
            "difficulty": "beginner",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    aws_subject = db_session.scalar(select(Subject).where(Subject.slug == "aws"))
    testing_subject = db_session.scalar(select(Subject).where(Subject.slug == "testing"))
    assert aws_subject is not None
    assert testing_subject is not None
    assert aws_subject.id != testing_subject.id


def test_subject_pages_show_namespace_workspace(client: TestClient) -> None:
    list_response = client.get("/subjects")

    assert list_response.status_code == 200
    assert "Subjects" in list_response.text
    assert "testing" in list_response.text
    assert "1 concepts" in list_response.text
    assert "1 questions" in list_response.text

    detail_response = client.get("/subjects/1")
    assert detail_response.status_code == 200
    assert "Concept performance" in detail_response.text
    assert "Start quick practice" in detail_response.text
    assert "Pytest Basics" in detail_response.text
    assert "What does pytest run?" in detail_response.text
    assert "Test quiz" in detail_response.text


def test_subject_review_uses_subject_queue(client: TestClient, db_session: Session) -> None:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    wrong_choice = db_session.scalar(
        select(Choice).where(Choice.question_id == question.id, Choice.is_correct.is_(False))
    )
    assert wrong_choice is not None
    client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(wrong_choice.id)},
    )

    response = client.post("/subjects/1/review", follow_redirects=False)

    assert response.status_code == 303
    review_quiz = db_session.scalar(
        select(Quiz)
        .where(Quiz.quiz_type == QuizType.PRACTICE, Quiz.assembly_strategy == "mastery_review")
        .options(selectinload(Quiz.questions), selectinload(Quiz.primary_subject))
    )
    assert review_quiz is not None
    assert response.headers["location"] == f"/quizzes/{review_quiz.id}/take"
    assert review_quiz.quiz_type == QuizType.PRACTICE
    assert review_quiz.is_disposable is True
    assert review_quiz.primary_subject is not None
    assert review_quiz.primary_subject.slug == "testing"
    assert [review_question.id for review_question in review_quiz.questions] == [question.id]


def test_subject_quick_practice_assembles_disposable_session(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post("/subjects/1/practice", follow_redirects=False)

    practice = db_session.scalar(
        select(Quiz)
        .where(Quiz.quiz_type == QuizType.PRACTICE)
        .options(selectinload(Quiz.questions), selectinload(Quiz.question_links))
    )
    assert practice is not None
    assert response.status_code == 303
    assert response.headers["location"] == f"/quizzes/{practice.id}/take"
    assert practice.is_disposable is True
    assert practice.assembly_strategy == "random_subject"
    assert [question.stem for question in practice.questions] == ["What does pytest run?"]
    assert practice.question_links[0].selection_reason.startswith("random_subject:")


def test_question_health_review_queue_is_automatic_after_submit(
    client: TestClient,
    db_session: Session,
) -> None:
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    wrong_choice = db_session.scalar(
        select(Choice).where(Choice.question_id == question.id, Choice.is_correct.is_(False))
    )
    assert wrong_choice is not None

    response = client.post(
        "/quizzes/1/submit",
        data={f"question_{question.id}": str(wrong_choice.id)},
    )

    assert response.status_code == 200
    db_session.refresh(question)
    assert question.health_state == QuestionHealthState.NEEDS_REVIEW
    detail_response = client.get("/subjects/1")
    assert detail_response.status_code == 200
    assert "question reviews" in detail_response.text
    assert "The first observed attempt missed this question." in detail_response.text


def test_e2e_question_first_practice_flow(client: TestClient, db_session: Session) -> None:
    practice_response = client.post("/subjects/1/practice", follow_redirects=False)
    practice_id = int(practice_response.headers["location"].split("/")[2])
    practice = db_session.scalar(
        select(Quiz)
        .where(Quiz.id == practice_id)
        .options(selectinload(Quiz.questions).selectinload(Question.choices))
    )
    assert practice is not None
    question = practice.questions[0]
    wrong_choice = next(choice for choice in question.choices if not choice.is_correct)

    result_response = client.post(
        f"/quizzes/{practice.id}/submit",
        data={f"question_{question.id}": str(wrong_choice.id)},
    )

    assert result_response.status_code == 200
    db_session.refresh(question)
    assert practice.quiz_type == QuizType.PRACTICE
    assert question.health_state == QuestionHealthState.NEEDS_REVIEW
    assert "Results" in result_response.text
