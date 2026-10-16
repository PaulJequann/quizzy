from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict, TypeGuard

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


class PaperQuestionPayload(TypedDict):
    choices: list[object]
    stem: str
    explanation: str
    key: int
    correct_choice_key: int


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
    if not _is_paper_question_payload(raw):
        raise ValueError
    return PaperQuestion(
        raw["key"],
        raw["stem"],
        raw["explanation"],
        _parse_question_choices(raw),
        raw["correct_choice_key"],
    )


def _parse_question_choices(raw: PaperQuestionPayload) -> tuple[PaperChoice, ...]:
    choices = tuple(_parse_paper_choice(choice) for choice in raw["choices"])
    _validate_question(raw["key"], raw["stem"], choices, raw["correct_choice_key"])
    return choices


def _is_paper_question_payload(raw: object) -> TypeGuard[PaperQuestionPayload]:
    return isinstance(raw, dict) and all(
        (
            isinstance(raw.get("choices"), list),
            isinstance(raw.get("stem"), str),
            isinstance(raw.get("explanation"), str),
            type(raw.get("key")) is int,
            type(raw.get("correct_choice_key")) is int,
        )
    )


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
