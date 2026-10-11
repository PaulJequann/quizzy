from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.boundary import study_operation
from app.database import get_db
from app.models import AttemptAnswer
from app.services import concept_scheduler_service, question_health_service
from app.services.question_health_service import QuestionIssueReport
from app.study_access import StudyOperation

router = APIRouter()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/attempt-answers/{answer_id}/retrieval-feedback")
@study_operation(StudyOperation.RECORD_PROGRESS)
async def record_retrieval_feedback(
    request: Request,
    answer_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    answer = db.get(AttemptAnswer, answer_id)
    if answer is None:
        raise HTTPException(status_code=404, detail="Attempt answer not found")
    form = await request.form()
    try:
        concept_scheduler_service.record_retrieval_feedback(
            db,
            attempt_answer_id=answer.id,
            friction_rating=_form_int(form, "retrieval_friction_rating"),
            response_time_ms=_form_optional_int(form, "response_time_ms"),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return redirect(f"/attempts/{answer.attempt_id}/results#answer-{answer.id}")


@router.post("/questions/{question_id}/report-issue")
@study_operation(StudyOperation.RECORD_PROGRESS)
async def report_question_issue(
    request: Request,
    question_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    form = await request.form()
    attempt_answer_id = _form_optional_int(form, "attempt_answer_id")
    if not request.state.access.can_report_question(attempt_answer_id):
        raise HTTPException(403, "Report feedback from one of your answers")
    try:
        question_health_service.report_question_issue(
            db,
            QuestionIssueReport(
                question_id=question_id,
                category=_form_text(form, "category"),
                note=_form_text(form, "note"),
                attempt_answer_id=attempt_answer_id,
                reported_from="detailed",
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return redirect(_issue_report_redirect(db, request, attempt_answer_id))


def _form_text(form, key: str, default: str = "") -> str:
    value = form.get(key, default)
    return value if isinstance(value, str) else default


def _form_int(form, key: str, default: int = 0) -> int:
    value = form.get(key, str(default))
    if not isinstance(value, str):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {key.replace('_', ' ')}.") from exc


def _form_optional_int(form, key: str) -> int | None:
    value = form.get(key, "")
    if value == "" or value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {key.replace('_', ' ')}.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {key.replace('_', ' ')}.") from exc


def _issue_report_redirect(
    db: Session,
    request: Request,
    attempt_answer_id: int | None,
) -> str:
    if attempt_answer_id is not None:
        answer = db.get(AttemptAnswer, attempt_answer_id)
        if answer is not None:
            return f"/attempts/{answer.attempt_id}/results#answer-{attempt_answer_id}"
    referer = request.headers.get("referer")
    if referer and _is_same_origin_redirect(referer, request):
        return referer
    return "/quizzes"


def _is_same_origin_redirect(target: str, request: Request) -> bool:
    parsed = urlsplit(target)
    if not parsed.netloc:
        return target.startswith("/")
    return parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
