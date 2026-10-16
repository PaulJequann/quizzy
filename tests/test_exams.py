from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.models import User
from app.database import get_session_factory
from app.main import create_app
from app.models import (
    Attempt,
    Choice,
    Difficulty,
    ExamAssignment,
    ExamSubmission,
    Question,
    QuestionStatus,
    Quiz,
    QuizQuestion,
)

pytestmark = pytest.mark.integration


def create_learner(email: str, name: str) -> int:
    with get_session_factory()() as accounts:
        learner = User(
            email=email,
            hashed_password="!",
            display_name=name,
            is_active=True,
            is_superuser=False,
        )
        accounts.add(learner)
        accounts.commit()
        return learner.id


def reviewer_browser():
    browser = TestClient(
        create_app(), headers={"origin": "http://testserver"}, follow_redirects=False
    )
    assert (
        browser.post(
            "/auth/login", data={"username": "parent@example.com", "password": "test-password"}
        ).status_code
        == 204
    )
    return browser


def learner_browser(learner_id: int):
    from app.auth.users import current_user

    app = create_app()
    app.dependency_overrides[current_user] = lambda: User(
        id=learner_id, is_active=True, is_superuser=False
    )
    return TestClient(app, headers={"origin": "http://testserver"}, follow_redirects=False)


def assign_exam(browser: TestClient, learner_id: int) -> int:
    form = browser.get("/quizzes/1/exam")
    assert form.status_code == 200
    response = browser.post(
        "/quizzes/1/exam",
        data={"learner_id": str(learner_id), "request_key": str(uuid4())},
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def add_grading_questions(quiz: Quiz) -> None:
    for order, stem in (
        (2, "What should the learner review?"),
        (3, "What can the learner skip?"),
    ):
        question = Question(
            subject=quiz.primary_subject,
            stem=stem,
            difficulty=Difficulty.BEGINNER,
            status=QuestionStatus.ACTIVE,
            choices=[
                Choice(text="Correct option", is_correct=True, order_index=1),
                Choice(text="Wrong option", is_correct=False, order_index=2),
            ],
        )
        quiz.question_links.append(QuizQuestion(question=question, order_index=order))


def test_exam_release_hides_result_and_keeps_practice_records_separate(db_session):
    learner_id = create_learner("child@example.com", "Child")
    quiz_row = db_session.scalar(select(Quiz))
    assert quiz_row is not None
    quiz_row.questions[0].explanation = "Secret explanation"
    db_session.commit()
    baseline_attempts = db_session.scalar(select(func.count(Attempt.id)))

    reviewer = reviewer_browser()
    exam_id = assign_exam(reviewer, learner_id)
    quiz_row.questions[0].stem = "Changed source after assignment"
    db_session.commit()
    assert reviewer.get(f"/exams/{exam_id}").status_code == 200

    learner = learner_browser(learner_id)
    take = learner.get(f"/exams/{exam_id}")
    assert take.status_code == 200
    assert "What does pytest run?" in take.text
    assert "Changed source after assignment" not in take.text
    assert "Secret explanation" not in take.text
    assert "Correct answer" not in take.text
    submit = learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"})
    assert submit.status_code == 303

    pending = learner.get(f"/exams/{exam_id}")
    assert pending.status_code == 200
    assert "Submitted for review" in pending.text
    for secret in ("Secret explanation", "Correct answer", "Reviewer note", "100%", "Tests"):
        assert secret not in pending.text
    assert learner.post(f"/exams/{exam_id}/release", data={"note": "forbidden"}).status_code == 403
    assert learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "2"}).status_code == 409

    review = reviewer.get(f"/exams/{exam_id}")
    assert review.status_code == 200
    assert "100.0%" in review.text
    assert "Secret explanation" in review.text
    released = reviewer.post(f"/exams/{exam_id}/release", data={"note": "Nice work."})
    assert released.status_code == 303
    retry = reviewer.post(f"/exams/{exam_id}/release", data={"note": "overwrite"})
    assert retry.status_code == 303
    released_page = reviewer.get(f"/exams/{exam_id}")
    assert "Nice work." in released_page.text
    assert "overwrite" not in released_page.text

    result = learner.get(f"/exams/{exam_id}")
    assert "100.0%" in result.text
    assert "Tests" in result.text
    assert "Secret explanation" in result.text
    assert "Nice work." in result.text
    assert "Test quiz" not in learner.get("/progress").text
    assert db_session.scalar(select(func.count(Attempt.id))) == baseline_attempts
    with get_session_factory()() as accounts:
        from app.study_access import resolve_study_access
        from app.workspace_database import workspace_session

        learner = accounts.get(User, learner_id)
        assert learner is not None
        access = resolve_study_access(accounts, learner)
    with workspace_session(access) as learner_db:
        assert learner_db.scalar(select(func.count(ExamAssignment.id))) == 1
        assert learner_db.scalar(select(func.count(ExamSubmission.id))) == 1
        assert learner_db.scalar(select(func.count(Attempt.id))) == 0


def test_exam_grades_correct_incorrect_and_unanswered_responses(db_session):
    learner_id = create_learner("child@example.com", "Child")
    quiz = db_session.scalar(select(Quiz))
    assert quiz is not None
    add_grading_questions(quiz)
    db_session.commit()

    reviewer = reviewer_browser()
    exam_id = assign_exam(reviewer, learner_id)
    learner = learner_browser(learner_id)
    submitted = learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "1", "answer_2": "2"})
    assert submitted.status_code == 303

    review = reviewer.get(f"/exams/{exam_id}")
    assert "33.33%" in review.text
    assert "1 of 3 correct" in review.text
    assert "Learner answer: Tests" in review.text
    assert "Correct answer: Tests" in review.text
    assert "Learner answer: Wrong option" in review.text
    assert "Learner answer: No answer" in review.text
    assert review.text.count("Incorrect") == 2


def test_exam_submission_rejects_questions_and_choices_outside_paper(db_session):
    learner_id = create_learner("child@example.com", "Child")
    reviewer = reviewer_browser()
    exam_id = assign_exam(reviewer, learner_id)
    learner = learner_browser(learner_id)

    unknown_question = learner.post(f"/exams/{exam_id}/submit", data={"answer_2": "1"})
    assert unknown_question.status_code == 400
    assert db_session.scalar(select(func.count(ExamSubmission.id))) == 0

    invalid_choice = learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "99"})
    assert invalid_choice.status_code == 400
    assert db_session.scalar(select(func.count(ExamSubmission.id))) == 0


def test_exam_authority_workspace_isolation_and_submission_retry(db_session):
    learner_id = create_learner("child@example.com", "Child")
    other_id = create_learner("other@example.com", "Other")
    reviewer = reviewer_browser()
    request_key = str(uuid4())
    first_assignment = reviewer.post(
        "/quizzes/1/exam",
        data={"learner_id": str(learner_id), "request_key": request_key},
    )
    assert first_assignment.status_code == 303
    exam_id = int(first_assignment.headers["location"].rsplit("/", 1)[1])
    reviewer.cookies.delete("quizzy_workspace")
    retry_assignment = reviewer.post(
        "/quizzes/1/exam",
        data={"learner_id": str(learner_id), "request_key": request_key},
    )
    assert retry_assignment.status_code == 303
    assert retry_assignment.headers["location"] == f"/exams/{exam_id}"

    learner = learner_browser(learner_id)
    identical_retry = learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"})
    assert identical_retry.status_code == 303
    assert learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"}).status_code == 303
    assert learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "2"}).status_code == 409
    assert learner.get("/accounts").status_code == 303

    other = learner_browser(other_id)
    assert other.get(f"/exams/{exam_id}").status_code == 404
    assert other.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"}).status_code == 404

    reviewer.cookies.set("quizzy_workspace", str(learner_id))
    assert reviewer.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"}).status_code == 403
    assert (
        reviewer.post(
            "/quizzes/1/exam",
            data={"learner_id": str(learner_id), "request_key": str(uuid4())},
        ).status_code
        == 403
    )
