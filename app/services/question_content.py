from collections.abc import Sequence
from typing import Any, Protocol

from app.models import Choice, Question, QuestionKeyPoint, QuestionType
from app.schemas import OpenEndedFields


class ChoiceContent(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def is_correct(self) -> bool: ...


class MultipleChoiceContent(Protocol):
    @property
    def choices(self) -> Sequence[ChoiceContent]: ...


type QuestionContent = OpenEndedFields | MultipleChoiceContent


def is_open_ended(content: QuestionContent) -> bool:
    return isinstance(content, OpenEndedFields)


def apply_content(question: Question, content: QuestionContent) -> None:
    if isinstance(content, OpenEndedFields):
        question.question_type = QuestionType.OPEN_ENDED
        question.exam_only = True
        question.model_answer = content.model_answer
        question.grading_notes = (content.grading_notes or "").strip() or None
        question.key_points = [
            QuestionKeyPoint(text=text, order_index=index)
            for index, text in enumerate(content.key_points, start=1)
        ]
        return
    question.question_type = QuestionType.MULTIPLE_CHOICE
    question.choices = [
        Choice(text=choice.text.strip(), is_correct=choice.is_correct, order_index=index)
        for index, choice in enumerate(content.choices, start=1)
    ]


def rubric_to_dict(question: Question) -> dict[str, Any]:
    return {
        "model_answer": question.model_answer,
        "grading_notes": question.grading_notes,
        "key_points": [key_point.text for key_point in question.key_points],
    }
