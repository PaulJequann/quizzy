from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.models import Choice, Question, QuestionType, Quiz
from app.schemas import MAX_KEY_POINTS, MIN_KEY_POINTS
from app.services.exam_errors import ExamError

PAPER_VERSION = 1
INCOMPLETE_QUESTION = "Every exam question needs a stem and a complete answer key"

PositiveKey = Annotated[StrictInt, Field(gt=0)]
FilledText = Annotated[StrictStr, Field(pattern=r"\S")]


class PaperPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaperChoice(PaperPart):
    key: PositiveKey
    text: StrictStr


class MultipleChoicePaperQuestion(PaperPart):
    type: Literal["multiple_choice"] = "multiple_choice"
    key: PositiveKey
    question_id: PositiveKey
    stem: FilledText
    explanation: StrictStr
    choices: tuple[PaperChoice, ...] = Field(min_length=2)
    correct_choice_key: StrictInt

    @model_validator(mode="after")
    def _one_correct_choice_among_unique_keys(self) -> Self:
        keys = _unique_keys([choice.key for choice in self.choices], "Choice keys must be unique")
        if self.correct_choice_key not in keys:
            raise ValueError("The correct choice key must name a choice")
        return self


class OpenEndedPaperQuestion(PaperPart):
    type: Literal["open_ended"] = "open_ended"
    key: PositiveKey
    question_id: PositiveKey
    stem: FilledText
    explanation: StrictStr
    model_answer: FilledText
    grading_notes: StrictStr
    key_points: tuple[FilledText, ...] = Field(min_length=MIN_KEY_POINTS, max_length=MAX_KEY_POINTS)


PaperQuestion = MultipleChoicePaperQuestion | OpenEndedPaperQuestion


class Paper(PaperPart):
    version: StrictInt
    questions: tuple[
        Annotated[PaperQuestion, Field(discriminator="type")],
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_question_keys(self) -> Self:
        _unique_keys([question.key for question in self.questions], "Question keys must be unique")
        return self


def _unique_keys(keys: list[int], message: str) -> set[int]:
    unique = set(keys)
    if len(unique) != len(keys):
        raise ValueError(message)
    return unique


def paper_from_quiz(quiz: Quiz) -> Paper:
    if not quiz.questions:
        raise ExamError("A published quiz needs at least one question before it can be assigned")
    return Paper(version=PAPER_VERSION, questions=_snapshot_questions(quiz.questions))


def _snapshot_questions(questions: list[Question]) -> tuple[PaperQuestion, ...]:
    try:
        return tuple(
            _QUESTION_BUILDERS[question.question_type](key, question)
            for key, question in enumerate(questions, start=1)
        )
    except ValidationError as exc:
        raise ExamError(INCOMPLETE_QUESTION) from exc


def _multiple_choice(key: int, question: Question) -> MultipleChoicePaperQuestion:
    ordered = sorted(question.choices, key=lambda item: (item.order_index, item.id))
    return MultipleChoicePaperQuestion(
        key=key,
        question_id=question.id,
        stem=question.stem,
        explanation=_text(question.explanation),
        choices=tuple(
            PaperChoice(key=index, text=choice.text)
            for index, choice in enumerate(ordered, start=1)
        ),
        correct_choice_key=_correct_choice_key(ordered),
    )


def _correct_choice_key(ordered: list[Choice]) -> int:
    correct = [index for index, choice in enumerate(ordered, start=1) if choice.is_correct]
    if len(correct) != 1:
        raise ExamError(INCOMPLETE_QUESTION)
    return correct[0]


def _open_ended(key: int, question: Question) -> OpenEndedPaperQuestion:
    return OpenEndedPaperQuestion(
        key=key,
        question_id=question.id,
        stem=question.stem,
        explanation=_text(question.explanation),
        model_answer=_text(question.model_answer),
        grading_notes=_text(question.grading_notes),
        key_points=tuple(key_point.text for key_point in question.key_points),
    )


def _text(value: str | None) -> str:
    return value or ""


_QUESTION_BUILDERS = {
    QuestionType.MULTIPLE_CHOICE: _multiple_choice,
    QuestionType.OPEN_ENDED: _open_ended,
}


def paper_json(paper: Paper) -> list[dict[str, object]]:
    return [question.model_dump(mode="json") for question in paper.questions]


def parse_paper(version: int, raw: object) -> Paper:
    if version != PAPER_VERSION:
        raise ExamError("This exam paper format is not supported")
    try:
        return Paper.model_validate({"version": version, "questions": raw})
    except ValidationError as exc:
        raise ExamError("This exam paper is invalid") from exc
