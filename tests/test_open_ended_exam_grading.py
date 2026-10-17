from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import (
    Difficulty,
    ExamResponseGrade,
    ExamSubmission,
    Question,
    QuestionKeyPoint,
    QuestionStatus,
    QuestionType,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.services.exam_errors import ExamError
from app.services.exam_grades import Grade
from app.services.exam_paper import parse_paper
from app.services.exam_results import ExamRecord, exam_result
from tests.test_exams import assign_exam, exam_question
from tests.workspace_clients import (
    create_learner,
    learner_browser,
    reviewer_browser,
    reviewer_session,
)

pytestmark = pytest.mark.integration

WRITTEN_STEM = "Explain how a plant makes its food."
MODEL_ANSWER = "Plants use light to turn water and carbon dioxide into sugar and oxygen."
GRADING_NOTES = "Accept glucose for sugar."
WRITTEN_EXPLANATION = "Photosynthesis happens in the leaves."
KEY_POINTS = ["Uses light energy", "Makes sugar", "Releases oxygen"]
LEARNER_ANSWER = "The plant takes in sunlight\nand makes sugar from it."
RELEASE_FORM = {
    "note": "Well done.",
    "grade_2_met": ["1", "2"],
    "grade_2_feedback": "You named light and sugar.",
    "grade_2_misconception": "",
}


@dataclass(frozen=True)
class SubmittedExam:
    learner_id: int
    exam_id: int
    reviewer: TestClient
    learner: TestClient


def seed_mixed_exam(learner_id: int) -> int:
    subject = Subject(name="Biology", slug="biology")
    written = Question(
        subject=subject,
        stem=WRITTEN_STEM,
        explanation=WRITTEN_EXPLANATION,
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        question_type=QuestionType.OPEN_ENDED,
        exam_only=True,
        model_answer=MODEL_ANSWER,
        grading_notes=GRADING_NOTES,
        key_points=[
            QuestionKeyPoint(text=text, order_index=index)
            for index, text in enumerate(KEY_POINTS, start=1)
        ],
    )
    choice = exam_question(subject, "What does pytest run?", ["Tests", "Backups", "Migrations"])
    quiz = Quiz(
        title="Plant exam",
        primary_subject=subject,
        difficulty=Difficulty.BEGINNER,
        quiz_type=QuizType.EXAM,
        status=QuizStatus.PUBLISHED,
        question_links=[
            QuizQuestion(question=choice, order_index=1),
            QuizQuestion(question=written, order_index=2),
        ],
    )
    with reviewer_session(learner_id) as db:
        db.add(quiz)
        db.commit()
        return quiz.id


def assigned_exam() -> SubmittedExam:
    learner_id = create_learner("child@example.com", "Child")
    reviewer = reviewer_browser(learner_id)
    exam_id = assign_exam(reviewer, seed_mixed_exam(learner_id))
    return SubmittedExam(learner_id, exam_id, reviewer, learner_browser(learner_id))


def submitted_exam() -> SubmittedExam:
    exam = assigned_exam()
    submit = exam.learner.post(
        f"/exams/{exam.exam_id}/submit", data={"answer_1": "1", "answer_2": LEARNER_ANSWER}
    )
    assert submit.status_code == 303
    return exam


def propose_grade(learner_id: int, met: list[int], feedback: str, misconception: str) -> None:
    with reviewer_session(learner_id) as db:
        submission = db.scalar(select(ExamSubmission))
        assert submission is not None
        db.add(
            ExamResponseGrade(
                submission_id=submission.id,
                question_key=2,
                key_points_met=met,
                feedback=feedback,
                misconception=misconception,
            )
        )
        db.commit()


def stored_grades(learner_id: int) -> list[tuple[int, list[int], str, str | None]]:
    with reviewer_session(learner_id) as db:
        return [
            (row.question_key, row.key_points_met, row.feedback, row.misconception)
            for row in db.scalars(select(ExamResponseGrade))
        ]


def released_count(learner_id: int) -> int:
    with reviewer_session(learner_id) as db:
        return db.scalar(
            select(func.count(ExamSubmission.id)).where(ExamSubmission.released_at.is_not(None))
        )


def test_take_page_hides_rubric_and_stores_written_answer(db_session):
    exam = assigned_exam()

    take = exam.learner.get(f"/exams/{exam.exam_id}")
    assert take.status_code == 200
    assert WRITTEN_STEM in take.text
    assert 'name="answer_2" maxlength="4000"' in take.text
    for secret in (MODEL_ANSWER, GRADING_NOTES, WRITTEN_EXPLANATION, *KEY_POINTS):
        assert secret not in take.text

    too_long = exam.learner.post(
        f"/exams/{exam.exam_id}/submit", data={"answer_1": "1", "answer_2": "x" * 4001}
    )
    assert too_long.status_code == 400
    assert too_long.json() == {"detail": "Written answers must be at most 4000 characters"}
    submit = exam.learner.post(
        f"/exams/{exam.exam_id}/submit", data={"answer_1": "1", "answer_2": LEARNER_ANSWER}
    )
    assert submit.status_code == 303
    with reviewer_session(exam.learner_id) as db:
        submission = db.scalar(select(ExamSubmission))
        assert submission is not None
        assert submission.responses_json == {"1": 1, "2": LEARNER_ANSWER}


def test_blank_written_answer_is_stored_as_no_answer(db_session):
    exam = assigned_exam()

    submit = exam.learner.post(f"/exams/{exam.exam_id}/submit", data={"answer_2": "   "})

    assert submit.status_code == 303
    with reviewer_session(exam.learner_id) as db:
        submission = db.scalar(select(ExamSubmission))
        assert submission is not None
        assert submission.responses_json == {"1": None, "2": None}
    review = exam.reviewer.get(f"/exams/{exam.exam_id}")
    assert review.text.count("No answer") == 2
    assert "0 of 3 · not graded yet" in review.text


def test_reviewer_edits_agent_proposal_and_releases_written_grade(db_session):
    exam = submitted_exam()
    with reviewer_session(exam.learner_id) as db:
        source = db.scalar(select(Question).where(Question.stem == WRITTEN_STEM))
        assert source is not None
        source.model_answer = "Changed after assignment"
        db.commit()
    propose_grade(exam.learner_id, [1], "Agent says mention oxygen.", "thinks plants eat soil")

    review = exam.reviewer.get(f"/exams/{exam.exam_id}")
    assert review.status_code == 200
    assert "The plant takes in sunlight\nand makes sugar from it." in review.text
    for rubric_text in (MODEL_ANSWER, GRADING_NOTES, *KEY_POINTS):
        assert rubric_text in review.text
    assert "Changed after assignment" not in review.text
    assert 'name="grade_2_met" value="1" checked' in review.text
    assert 'name="grade_2_met" value="2" >' in review.text
    assert "Agent says mention oxygen.</textarea>" in review.text
    assert 'value="thinks plants eat soil"' in review.text
    assert "66.67%" in review.text
    assert "1 of 2 correct" in review.text

    released = exam.reviewer.post(f"/exams/{exam.exam_id}/release", data=RELEASE_FORM)

    assert released.status_code == 303
    assert stored_grades(exam.learner_id) == [(2, [1, 2], "You named light and sugar.", None)]
    result = exam.learner.get(f"/exams/{exam.exam_id}")
    assert result.status_code == 200
    assert "83.33%" in result.text
    assert "2 of 2 correct" in result.text
    assert "2 of 3 key points" in result.text
    assert result.text.count(">Met<") == 2
    assert result.text.count(">Missed<") == 1
    for shown in ("You named light and sugar.", "Well done.", WRITTEN_EXPLANATION, *KEY_POINTS):
        assert shown in result.text
    for hidden in (MODEL_ANSWER, GRADING_NOTES, "thinks plants eat soil", "Agent says"):
        assert hidden not in result.text


@pytest.mark.parametrize(
    ("form", "detail"),
    [
        ({"note": "No grades"}, "Grade every open-ended answer before release"),
        (
            {**RELEASE_FORM, "grade_2_feedback": "   "},
            "Write feedback for every graded answer",
        ),
        (
            {**RELEASE_FORM, "grade_2_met": ["4"]},
            "Key points met must be distinct key point numbers from the rubric",
        ),
        (
            {**RELEASE_FORM, "grade_2_met": ["0"]},
            "Key points met must be distinct key point numbers from the rubric",
        ),
        (
            {**RELEASE_FORM, "grade_2_met": ["1", "1"]},
            "Key points met must be distinct key point numbers from the rubric",
        ),
        (
            {**RELEASE_FORM, "grade_1_feedback": "Choice grades itself"},
            "Grades apply only to open-ended questions in this exam",
        ),
        (
            {**RELEASE_FORM, "grade_2_misconception": "x" * 201},
            "Misconception must be at most 200 characters",
        ),
        ({**RELEASE_FORM, "grade_2_met": ["one"]}, "Invalid exam form"),
    ],
)
def test_release_refuses_missing_or_invalid_grades(db_session, form, detail):
    exam = submitted_exam()

    response = exam.reviewer.post(f"/exams/{exam.exam_id}/release", data=form)

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert released_count(exam.learner_id) == 0
    assert stored_grades(exam.learner_id) == []


def test_released_grades_are_locked_by_policy(db_session):
    exam = submitted_exam()
    assert exam.reviewer.post(f"/exams/{exam.exam_id}/release", data=RELEASE_FORM).status_code == (
        303
    )

    retry = exam.reviewer.post(
        f"/exams/{exam.exam_id}/release",
        data={**RELEASE_FORM, "grade_2_met": ["3"], "grade_2_feedback": "Rewritten"},
    )

    assert retry.status_code == 303
    assert stored_grades(exam.learner_id) == [(2, [1, 2], "You named light and sugar.", None)]
    with reviewer_session(exam.learner_id) as db:
        grade = db.scalar(select(ExamResponseGrade))
        assert grade is not None
        grade.feedback = "Changed after release"
        with pytest.raises(ValueError, match="Released exam results cannot change"):
            db.commit()
    with reviewer_session(exam.learner_id) as db:
        submission = db.scalar(select(ExamSubmission))
        assert submission is not None
        db.add(
            ExamResponseGrade(
                submission_id=submission.id, question_key=1, key_points_met=[], feedback="Late"
            )
        )
        with pytest.raises(ValueError, match="Released exam results cannot change"):
            db.commit()
    with reviewer_session(exam.learner_id) as db:
        submission = db.scalar(select(ExamSubmission))
        assert submission is not None
        submission.review_note = "Changed after release"
        with pytest.raises(ValueError, match="Released exam results cannot change"):
            db.commit()
    assert stored_grades(exam.learner_id) == [(2, [1, 2], "You named light and sugar.", None)]


def written_paper_question(**overrides) -> dict[str, object]:
    return {
        "type": "open_ended",
        "key": 1,
        "question_id": 7,
        "stem": WRITTEN_STEM,
        "explanation": "",
        "model_answer": MODEL_ANSWER,
        "grading_notes": "",
        "key_points": KEY_POINTS,
        **overrides,
    }


def test_paper_parses_written_question_snapshot():
    paper = parse_paper(1, [written_paper_question()])

    question = paper.questions[0]
    assert question.type == "open_ended"
    assert question.key_points == ("Uses light energy", "Makes sugar", "Releases oxygen")


@pytest.mark.parametrize(
    "raw",
    [
        [written_paper_question(key_points=["Only one"])],
        [written_paper_question(model_answer="  ")],
        [written_paper_question(key=True)],
        [written_paper_question(type="essay")],
        [written_paper_question(), written_paper_question(question_id=8)],
        [written_paper_question(choices=[])],
        [],
    ],
)
def test_paper_rejects_malformed_written_snapshots(raw):
    with pytest.raises(ExamError, match="This exam paper is invalid"):
        parse_paper(1, raw)


@pytest.mark.parametrize(
    ("key_point_count", "met", "score", "correct_count"),
    [
        (4, (1, 2), 50.0, 0),
        (4, (1, 2, 3), 75.0, 1),
        (2, (2,), 50.0, 0),
        (3, (1, 3), 66.67, 1),
    ],
)
def test_written_answer_counts_as_correct_only_above_half(
    key_point_count, met, score, correct_count
):
    key_points = [f"Point {number}" for number in range(1, key_point_count + 1)]
    paper = parse_paper(1, [written_paper_question(key_points=key_points)])
    record = ExamRecord(1, "Plant exam", paper, {1: "Answer"}, {1: Grade(met, "Feedback")}, "")

    result = exam_result(record)

    assert (result.score, result.correct_count) == (score, correct_count)
