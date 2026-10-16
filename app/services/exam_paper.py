from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from app.models import Question, Quiz
from app.services.exam_errors import ExamError

PAPER_VERSION = 1


@dataclass(frozen=True)
class PaperChoice:
    key: int
    text: str


@dataclass(frozen=True)
class PaperQuestion:
    key: int
    stem: str
    explanation: str
    choices: tuple[PaperChoice, ...]
    correct_choice_key: int


@dataclass(frozen=True)
class Paper:
    version: int
    questions: tuple[PaperQuestion, ...]


@dataclass(frozen=True)
class RawQuestionFields:
    choices: object
    stem: object
    explanation: object
    key: object
    correct_key: object

    def validate(self) -> None:
        _validate_raw_question_fields(self)


def paper_from_quiz(quiz: Quiz) -> Paper:
    ordered = sorted(quiz.questions, key=lambda item: (item.order_index, item.id))
    if not ordered:
        raise ExamError("A published quiz needs at least one question before it can be assigned")
    questions = tuple(
        _question_from_quiz(key, question) for key, question in enumerate(ordered, start=1)
    )
    return Paper(PAPER_VERSION, questions)


def _question_from_quiz(key: int, question: Question) -> PaperQuestion:
    ordered = sorted(question.choices, key=lambda item: (item.order_index, item.id))
    choices = tuple(
        PaperChoice(choice_key, choice.text) for choice_key, choice in enumerate(ordered, start=1)
    )
    correct = tuple(
        paper_choice.key
        for paper_choice, quiz_choice in zip(choices, ordered, strict=True)
        if quiz_choice.is_correct
    )
    _validate_quiz_question(question.stem, choices, correct)
    return PaperQuestion(key, question.stem, question.explanation or "", choices, correct[0])


def _validate_quiz_question(
    stem: str, choices: tuple[PaperChoice, ...], correct: tuple[int, ...]
) -> None:
    if not stem.strip():
        raise ExamError("Every exam question needs a stem, choices, and one correct answer")
    if len(choices) < 2 or len(correct) != 1:
        raise ExamError("Every exam question needs a stem, choices, and one correct answer")


def paper_json(paper: Paper) -> list[dict[str, object]]:
    return [
        {
            "key": question.key,
            "stem": question.stem,
            "explanation": question.explanation,
            "choices": [{"key": choice.key, "text": choice.text} for choice in question.choices],
            "correct_choice_key": question.correct_choice_key,
        }
        for question in paper.questions
    ]


def parse_paper(version: int, raw: object) -> Paper:
    if version != PAPER_VERSION:
        raise ExamError("This exam paper format is not supported")
    if not isinstance(raw, list) or not raw:
        raise ExamError("This exam paper format is not supported")
    questions = _parse_questions_safely(raw)
    if not _unique_question_keys(questions):
        raise ExamError("This exam paper is invalid")
    return Paper(version, tuple(questions))


def _parse_questions_safely(raw: Sequence[object]) -> list[PaperQuestion]:
    try:
        return _parse_questions(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExamError("This exam paper is invalid") from exc


def _parse_questions(raw: Sequence[object]) -> list[PaperQuestion]:
    return [_parse_paper_question(item) for item in raw]


def _unique_question_keys(questions: list[PaperQuestion]) -> bool:
    keys = [question.key for question in questions]
    return len(set(keys)) == len(keys)


def _parse_paper_question(raw: object) -> PaperQuestion:
    if not isinstance(raw, dict):
        raise ValueError
    key, stem, explanation, correct_key, raw_choices = _question_fields(raw)
    choices = tuple(_parse_paper_choice(choice) for choice in raw_choices)
    _validate_question(key, stem, choices, correct_key)
    return PaperQuestion(key, stem, explanation, choices, correct_key)


def _question_fields(raw: dict) -> tuple[int, str, str, int, list[object]]:
    fields = RawQuestionFields(
        raw.get("choices"),
        raw.get("stem"),
        raw.get("explanation"),
        raw.get("key"),
        raw.get("correct_choice_key"),
    )
    fields.validate()
    return cast(
        tuple[int, str, str, int, list[object]],
        (fields.key, fields.stem, fields.explanation, fields.correct_key, fields.choices),
    )


def _validate_raw_question_fields(fields: RawQuestionFields) -> None:
    if not all(
        (
            isinstance(fields.choices, list),
            isinstance(fields.stem, str),
            isinstance(fields.explanation, str),
            type(fields.key) is int,
            type(fields.correct_key) is int,
        )
    ):
        raise ValueError


def _validate_question(
    key: int, stem: str, choices: tuple[PaperChoice, ...], correct_key: int
) -> None:
    _validate_question_identity(key, stem)
    _validate_question_choices(choices, correct_key)


def _validate_question_identity(key: int, stem: str) -> None:
    if key <= 0:
        raise ValueError
    if not stem.strip():
        raise ValueError


def _validate_question_choices(choices: tuple[PaperChoice, ...], correct_key: int) -> None:
    if len(choices) < 2:
        raise ValueError
    choice_keys = {choice.key for choice in choices}
    if len(choice_keys) != len(choices):
        raise ValueError
    if correct_key not in choice_keys:
        raise ValueError


def _parse_paper_choice(raw: object) -> PaperChoice:
    if not isinstance(raw, dict):
        raise ValueError
    key = raw.get("key")
    text = raw.get("text")
    if type(key) is not int or not isinstance(text, str):
        raise ValueError
    return PaperChoice(key, text)
