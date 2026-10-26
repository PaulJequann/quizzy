from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from app.services.exam_grades import Grade
from app.services.exam_paper import (
    MultipleChoicePaperQuestion,
    OpenEndedPaperQuestion,
    Paper,
)
from app.services.exam_responses import ResponseValue


@dataclass(frozen=True)
class ChoiceFeedback:
    kind: ClassVar[str] = "multiple_choice"
    key: int
    question: str
    selected_choice: str | None
    correct_choice: str
    is_correct: bool
    explanation: str

    @property
    def score(self) -> float:
        return 1.0 if self.is_correct else 0.0


@dataclass(frozen=True)
class KeyPointMark:
    number: int
    text: str
    met: bool


@dataclass(frozen=True)
class WrittenFeedback:
    kind: ClassVar[str] = "open_ended"
    key: int
    question: str
    answer: str | None
    key_points: tuple[KeyPointMark, ...]
    feedback: str
    graded: bool
    explanation: str

    @property
    def met_count(self) -> int:
        return sum(mark.met for mark in self.key_points)

    @property
    def score(self) -> float:
        return self.met_count / len(self.key_points)

    @property
    def is_correct(self) -> bool:
        return self.met_count * 2 > len(self.key_points)


type AnswerFeedback = ChoiceFeedback | WrittenFeedback


@dataclass(frozen=True)
class ExamResultView:
    id: int
    title: str
    score: float
    correct_count: int
    total_questions: int
    feedback: tuple[AnswerFeedback, ...]
    review_note: str
    actor_id: int | None
    actor_role: str
    submitted_at: datetime
    actor_name: str | None


@dataclass(frozen=True)
class WrittenReview:
    kind: ClassVar[str] = "open_ended"
    result: WrittenFeedback
    model_answer: str
    grading_notes: str
    misconception: str | None


type ReviewItem = ChoiceFeedback | WrittenReview


@dataclass(frozen=True)
class ExamRecord:
    id: int
    title: str
    paper: Paper
    responses: Mapping[int, ResponseValue]
    grades: Mapping[int, Grade]
    review_note: str
    actor_id: int | None
    actor_role: str
    submitted_at: datetime
    actor_name: str | None


def exam_result(record: ExamRecord) -> ExamResultView:
    feedback = tuple(_feedback(record, question) for question in record.paper.questions)
    return ExamResultView(
        record.id,
        record.title,
        round(sum(item.score for item in feedback) / len(feedback) * 100, 2),
        sum(item.is_correct for item in feedback),
        len(feedback),
        feedback,
        record.review_note,
        record.actor_id,
        record.actor_role,
        record.submitted_at,
        record.actor_name,
    )


def review_items(record: ExamRecord) -> tuple[ReviewItem, ...]:
    return tuple(_review_item(record, question) for question in record.paper.questions)


def _review_item(
    record: ExamRecord, question: MultipleChoicePaperQuestion | OpenEndedPaperQuestion
) -> ReviewItem:
    response = record.responses.get(question.key)
    if isinstance(question, MultipleChoicePaperQuestion):
        return _choice_feedback(question, response)
    grade = record.grades.get(question.key)
    return WrittenReview(
        _written_feedback(question, response, grade),
        question.model_answer,
        question.grading_notes,
        grade.misconception if grade else None,
    )


def _feedback(
    record: ExamRecord, question: MultipleChoicePaperQuestion | OpenEndedPaperQuestion
) -> AnswerFeedback:
    response = record.responses.get(question.key)
    if isinstance(question, OpenEndedPaperQuestion):
        return _written_feedback(question, response, record.grades.get(question.key))
    return _choice_feedback(question, response)


def _choice_feedback(
    question: MultipleChoicePaperQuestion, response: ResponseValue
) -> ChoiceFeedback:
    choices = {choice.key: choice.text for choice in question.choices}
    return ChoiceFeedback(
        question.key,
        question.stem,
        choices.get(response) if isinstance(response, int) else None,
        choices[question.correct_choice_key],
        response == question.correct_choice_key,
        question.explanation,
    )


def _written_feedback(
    question: OpenEndedPaperQuestion, response: ResponseValue, grade: Grade | None
) -> WrittenFeedback:
    met = set(grade.key_points_met) if grade else set()
    return WrittenFeedback(
        question.key,
        question.stem,
        response if isinstance(response, str) else None,
        tuple(
            KeyPointMark(number, text, number in met)
            for number, text in enumerate(question.key_points, start=1)
        ),
        grade.feedback if grade else "",
        grade is not None,
        question.explanation,
    )
