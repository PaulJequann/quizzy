from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

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
    QuizStatus,
    QuizType,
    Subject,
)
from app.services.exam_paper import paper_from_quiz
from tests.workspace_clients import (
    create_learner,
    learner_browser,
    reviewer_browser,
    reviewer_session,
)

pytestmark = pytest.mark.integration


def exam_question(subject: Subject, stem: str, choices: list[str], explanation: str | None = None):
    return Question(
        subject=subject,
        stem=stem,
        explanation=explanation,
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        exam_only=True,
        choices=[
            Choice(text=text, is_correct=index == 1, order_index=index)
            for index, text in enumerate(choices, start=1)
        ],
    )


def seed_exam_quiz(learner_id: int, *, grading_questions: bool = False) -> int:
    subject = Subject(name="testing", slug="testing")
    questions = [
        exam_question(
            subject,
            "What does pytest run?",
            ["Tests", "Backups", "Migrations", "Containers"],
            "Secret explanation",
        )
    ]
    if grading_questions:
        questions += [
            exam_question(subject, stem, ["Correct option", "Wrong option"])
            for stem in ("What should the learner review?", "What can the learner skip?")
        ]
    quiz = Quiz(
        title="Unit exam",
        primary_subject=subject,
        difficulty=Difficulty.BEGINNER,
        quiz_type=QuizType.EXAM,
        status=QuizStatus.PUBLISHED,
        question_links=[
            QuizQuestion(question=question, order_index=index)
            for index, question in enumerate(questions, start=1)
        ],
    )
    with reviewer_session(learner_id) as db:
        db.add(quiz)
        db.commit()
        return quiz.id


def assign_exam(browser: TestClient, quiz_id: int, request_key: str | None = None) -> int:
    form = browser.get(f"/quizzes/{quiz_id}/exam")
    assert form.status_code == 200
    response = browser.post(
        f"/quizzes/{quiz_id}/exam", data={"request_key": request_key or str(uuid4())}
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def test_exam_release_hides_result_and_keeps_practice_records_separate(db_session):
    learner_id = create_learner("child@example.com", "Child")
    quiz_id = seed_exam_quiz(learner_id)

    reviewer = reviewer_browser(learner_id)
    exam_id = assign_exam(reviewer, quiz_id)
    with reviewer_session(learner_id) as db:
        quiz_row = db.get(Quiz, quiz_id)
        assert quiz_row is not None
        quiz_row.questions[0].stem = "Changed source after assignment"
        db.commit()
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
    assert "Unit exam" not in learner.get("/progress").text
    with reviewer_session(learner_id) as learner_db:
        assert learner_db.scalar(select(func.count(ExamAssignment.id))) == 1
        assert learner_db.scalar(select(func.count(ExamSubmission.id))) == 1
        assert learner_db.scalar(select(func.count(Attempt.id))) == 1
        attempt = learner_db.scalar(select(Attempt).options(selectinload(Attempt.answers)))
        assert attempt is not None
        assert (attempt.session_type, attempt.score, attempt.correct_count) == ("exam", 100.0, 1)
        assert [
            (answer.question_stem_snapshot, answer.score, answer.is_correct)
            for answer in attempt.answers
        ] == [("What does pytest run?", 1.0, True)]
    attempt_page = learner.get(f"/attempts/{attempt.id}/results")
    assert attempt_page.status_code == 303
    assert attempt_page.headers["location"] == f"/exams/{exam_id}"


def test_exam_grades_correct_incorrect_and_unanswered_responses(db_session):
    learner_id = create_learner("child@example.com", "Child")
    quiz_id = seed_exam_quiz(learner_id, grading_questions=True)

    reviewer = reviewer_browser(learner_id)
    exam_id = assign_exam(reviewer, quiz_id)
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
    exam_id = assign_exam(reviewer_browser(learner_id), seed_exam_quiz(learner_id))
    learner = learner_browser(learner_id)

    unknown_question = learner.post(f"/exams/{exam_id}/submit", data={"answer_2": "1"})
    assert unknown_question.status_code == 400
    invalid_choice = learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "99"})
    assert invalid_choice.status_code == 400
    with reviewer_session(learner_id) as learner_db:
        assert learner_db.scalar(select(func.count(ExamSubmission.id))) == 0


def test_exam_authority_workspace_isolation_and_submission_retry(db_session):
    learner_id = create_learner("child@example.com", "Child")
    other_id = create_learner("other@example.com", "Other")
    quiz_id = seed_exam_quiz(learner_id)
    reviewer = reviewer_browser(learner_id)
    request_key = str(uuid4())
    exam_id = assign_exam(reviewer, quiz_id, request_key)
    retry_assignment = reviewer.post(f"/quizzes/{quiz_id}/exam", data={"request_key": request_key})
    assert retry_assignment.status_code == 303
    assert retry_assignment.headers["location"] == f"/exams/{exam_id}"

    learner = learner_browser(learner_id)
    identical_retry = learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"})
    assert identical_retry.status_code == 303
    assert learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"}).status_code == 303
    assert learner.post(f"/exams/{exam_id}/submit", data={"answer_1": "2"}).status_code == 409
    assert learner.get("/accounts").status_code == 303
    assert learner.get(f"/quizzes/{quiz_id}/exam").status_code == 403
    assert (
        learner.post(f"/quizzes/{quiz_id}/exam", data={"request_key": request_key}).status_code
        == 403
    )

    other = learner_browser(other_id)
    assert other.get(f"/exams/{exam_id}").status_code == 404
    assert other.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"}).status_code == 404

    assert reviewer.post(f"/exams/{exam_id}/submit", data={"answer_1": "1"}).status_code == 403
    assert reviewer.get("/quizzes/1/exam").status_code == 404
    own_workspace = reviewer_browser()
    assert own_workspace.get(f"/quizzes/{quiz_id}/exam").status_code == 403
    assert (
        own_workspace.post(f"/quizzes/{quiz_id}/exam", data={"request_key": str(uuid4())})
    ).status_code == 403


def test_exam_paper_preserves_source_quiz_question_order(db_session):
    quiz = db_session.scalar(select(Quiz))
    assert quiz is not None
    source_question = quiz.questions[0]
    source_question.stem = "Source quiz second question"
    quiz.question_links[0].order_index = 1
    first_question = Question(
        subject=quiz.primary_subject,
        stem="Source quiz first question",
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        choices=[
            Choice(text="Correct option", is_correct=True, order_index=1),
            Choice(text="Wrong option", is_correct=False, order_index=2),
        ],
    )
    quiz.question_links.append(QuizQuestion(question=first_question, order_index=0))
    other_quiz = Quiz(
        title="Other quiz",
        primary_subject=quiz.primary_subject,
        question_links=[
            QuizQuestion(question=source_question, order_index=0),
            QuizQuestion(question=first_question, order_index=1),
        ],
    )
    db_session.add(other_quiz)
    db_session.commit()
    db_session.expire(quiz, ["questions"])

    paper = paper_from_quiz(quiz)

    assert [question.stem for question in paper.questions] == [
        "Source quiz first question",
        "Source quiz second question",
    ]
