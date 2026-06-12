from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import AttemptAnswer, Choice, Concept, Question, Quiz, QuizQuestion, Subject


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
        .where(Quiz.title == "Weak Concept Review")
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
        .where(Quiz.title == "Weak Concept Review")
        .options(selectinload(Quiz.questions), selectinload(Quiz.primary_subject))
    )
    assert review_quiz is not None
    assert response.headers["location"] == f"/quizzes/{review_quiz.id}/take"
    assert review_quiz.primary_subject is not None
    assert review_quiz.primary_subject.slug == "testing"
    assert [review_question.id for review_question in review_quiz.questions] == [question.id]
