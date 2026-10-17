from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExamResponseGrade
from app.services.exam_errors import ExamError
from app.services.exam_paper import OpenEndedPaperQuestion, Paper

MAX_FEEDBACK_LENGTH = 2000
MAX_MISCONCEPTION_LENGTH = 200


@dataclass(frozen=True)
class Grade:
    key_points_met: tuple[int, ...]
    feedback: str
    misconception: str | None = None


@dataclass(frozen=True)
class ExamGrading:
    note: str
    grades: Mapping[int, Grade]


def validated_grades(
    paper: Paper, grades: Mapping[int, Grade], *, complete: bool
) -> dict[int, Grade]:
    written = {
        question.key: question
        for question in paper.questions
        if isinstance(question, OpenEndedPaperQuestion)
    }
    _require_graded_keys(set(grades), set(written), complete=complete)
    return {key: _validated_grade(written[key], grade) for key, grade in grades.items()}


def _require_graded_keys(graded: set[int], written: set[int], *, complete: bool) -> None:
    if not graded <= written:
        raise ExamError("Grades apply only to open-ended questions in this exam")
    if complete and graded != written:
        raise ExamError("Grade every open-ended answer before release")


def _validated_grade(question: OpenEndedPaperQuestion, grade: Grade) -> Grade:
    met = _key_points_met(question, grade.key_points_met)
    return Grade(met, _feedback(grade.feedback), _misconception(grade))


def _key_points_met(question: OpenEndedPaperQuestion, met: tuple[int, ...]) -> tuple[int, ...]:
    rubric_numbers = set(range(1, len(question.key_points) + 1))
    if sorted(set(met) & rubric_numbers) != sorted(met):
        raise ExamError("Key points met must be distinct key point numbers from the rubric")
    return tuple(sorted(met))


def _feedback(text: str) -> str:
    feedback = text.strip()
    if not feedback:
        raise ExamError("Write feedback for every graded answer")
    if len(feedback) > MAX_FEEDBACK_LENGTH:
        raise ExamError(f"Feedback must be at most {MAX_FEEDBACK_LENGTH} characters")
    return feedback


def _misconception(grade: Grade) -> str | None:
    misconception = (grade.misconception or "").strip()
    if len(misconception) > MAX_MISCONCEPTION_LENGTH:
        raise ExamError(f"Misconception must be at most {MAX_MISCONCEPTION_LENGTH} characters")
    return misconception or None


def load_grades(db: Session, submission_id: int) -> dict[int, Grade]:
    return {
        row.question_key: Grade(tuple(row.key_points_met), row.feedback, row.misconception)
        for row in _grade_rows(db, submission_id)
    }


def save_grades(db: Session, submission_id: int, grades: Mapping[int, Grade]) -> None:
    rows = {row.question_key: row for row in _grade_rows(db, submission_id)}
    for key, grade in grades.items():
        row = rows.get(key) or ExamResponseGrade(submission_id=submission_id, question_key=key)
        row.key_points_met = list(grade.key_points_met)
        row.feedback = grade.feedback
        row.misconception = grade.misconception
        db.add(row)


def _grade_rows(db: Session, submission_id: int) -> list[ExamResponseGrade]:
    return list(
        db.scalars(
            select(ExamResponseGrade).where(ExamResponseGrade.submission_id == submission_id)
        )
    )
