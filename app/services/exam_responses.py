from collections.abc import Mapping

from app.services.exam_errors import ExamError
from app.services.exam_paper import (
    MultipleChoicePaperQuestion,
    OpenEndedPaperQuestion,
    Paper,
    PaperQuestion,
)

MAX_WRITTEN_ANSWER_LENGTH = 4000

type ResponseValue = int | str | None


def read_form_answers(paper: Paper, answers: Mapping[int, str]) -> dict[int, ResponseValue]:
    questions = _questions_by_key(paper)
    _validate_response_keys(questions, answers)
    return {key: _form_value(questions[key], raw) for key, raw in answers.items()}


def _form_value(question: PaperQuestion, raw: str) -> ResponseValue:
    if not raw:
        return None
    if isinstance(question, OpenEndedPaperQuestion):
        return raw
    return _form_choice_key(raw)


def _form_choice_key(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ExamError("Response includes an invalid choice key") from exc


def normalize_responses(
    paper: Paper, responses: Mapping[int, ResponseValue]
) -> dict[str, ResponseValue]:
    questions = _questions_by_key(paper)
    _validate_response_keys(questions, responses)
    normalized: dict[str, ResponseValue] = {str(key): None for key in questions}
    for key, value in responses.items():
        normalized[str(key)] = _normalize_value(questions[key], value)
    return normalized


def _questions_by_key(paper: Paper) -> dict[int, PaperQuestion]:
    return {question.key: question for question in paper.questions}


def _validate_response_keys(
    questions: dict[int, PaperQuestion], responses: Mapping[int, object]
) -> None:
    for key in responses:
        if type(key) is not int:
            raise ExamError("Response includes an invalid question key")
        if key not in questions:
            raise ExamError("Response includes a question outside this exam")


def _normalize_value(question: PaperQuestion, value: ResponseValue) -> ResponseValue:
    if value is None:
        return None
    if isinstance(question, MultipleChoicePaperQuestion):
        return _choice_key(question, value)
    return _written_answer(value)


def _choice_key(question: MultipleChoicePaperQuestion, value: int | str) -> int:
    if type(value) is not int:
        raise ExamError("Response includes an invalid choice key")
    if value not in {choice.key for choice in question.choices}:
        raise ExamError("Choose an answer from the exam question")
    return value


def _written_answer(value: int | str) -> str | None:
    if not isinstance(value, str):
        raise ExamError("Response includes an invalid written answer")
    if len(value) > MAX_WRITTEN_ANSWER_LENGTH:
        raise ExamError(f"Written answers must be at most {MAX_WRITTEN_ANSWER_LENGTH} characters")
    return value if value.strip() else None
