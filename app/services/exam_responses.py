from collections.abc import Mapping

from app.services.exam_errors import ExamError
from app.services.exam_paper import Paper, PaperQuestion


def normalize_responses(paper: Paper, responses: Mapping[int, int | None]) -> dict[str, int | None]:
    questions = {question.key: question for question in paper.questions}
    _validate_response_keys(questions, responses)
    normalized: dict[str, int | None] = {str(key): None for key in questions}
    for key, selected in responses.items():
        _validate_response(questions[key], selected)
        normalized[str(key)] = selected
    return normalized


def _validate_response_keys(
    questions: dict[int, PaperQuestion], responses: Mapping[int, int | None]
) -> None:
    for key in responses:
        if type(key) is not int:
            raise ExamError("Response includes an invalid question key")
        if key not in questions:
            raise ExamError("Response includes a question outside this exam")


def _validate_response(question: PaperQuestion, selected: int | None) -> None:
    if selected is None:
        return
    if type(selected) is not int:
        raise ExamError("Response includes an invalid choice key")
    if selected not in {choice.key for choice in question.choices}:
        raise ExamError("Choose an answer from the exam question")
