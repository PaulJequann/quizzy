from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.boundary import study_operation
from app.database import get_db
from app.services import exam_service
from app.study_access import StudyAccessDenied, StudyOperation
from app.templating import templates
from app.workspace_database import WorkspaceSession

router = APIRouter()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _exam_template(page: exam_service.ExamPage) -> str:
    if isinstance(page, exam_service.ExamTakeView):
        return "exams/take.html"
    if isinstance(page, exam_service.ExamPendingView):
        return "exams/pending.html"
    if isinstance(page, exam_service.ExamAwaitingView):
        return "exams/awaiting.html"
    if isinstance(page, exam_service.ExamReviewView):
        return "exams/review.html"
    return "exams/result.html"


def _exam_responses(form) -> dict[int, int | None]:
    responses: dict[int, int | None] = {}
    for field, raw in form.items():
        if not field.startswith("answer_"):
            continue
        if not isinstance(raw, str):
            raise HTTPException(400, "Invalid exam response")
        try:
            question_key = int(field.removeprefix("answer_"))
            responses[question_key] = int(raw) if raw else None
        except ValueError as exc:
            raise HTTPException(400, "Invalid exam response") from exc
    return responses


def _call_exam(operation):
    try:
        return operation()
    except (ValueError, StudyAccessDenied) as exc:
        raise HTTPException(_exam_error_status(exc), str(exc)) from exc


def _exam_error_status(error: Exception) -> int:
    statuses = (
        (exam_service.ExamNotFound, 404),
        (exam_service.ExamAlreadySubmitted, 409),
        (StudyAccessDenied, 403),
    )
    return next((status for error_type, status in statuses if isinstance(error, error_type)), 400)


@router.get("/exams", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def exams_list(request: Request, db: Annotated[WorkspaceSession, Depends(get_db)]):
    exams = exam_service.list_exams(db, request.state.access)
    return templates.TemplateResponse(request, "exams/list.html", {"exams": exams})


@router.get("/quizzes/{quiz_id}/exam", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE_EXAMS)
def exam_assignment_form(
    request: Request, quiz_id: int, db: Annotated[WorkspaceSession, Depends(get_db)]
):
    quiz = _call_exam(lambda: exam_service.assignable_exam_quiz(db, quiz_id))
    return templates.TemplateResponse(
        request, "exams/assign.html", {"quiz": quiz, "request_key": uuid4()}
    )


@router.post("/quizzes/{quiz_id}/exam")
@study_operation(StudyOperation.MANAGE_EXAMS)
def assign_exam(
    request: Request,
    quiz_id: int,
    db: Annotated[WorkspaceSession, Depends(get_db)],
    request_key: Annotated[str, Form()],
):
    assignment = _call_exam(
        lambda: exam_service.assign_exam(
            db, request.state.access, source_quiz_id=quiz_id, request_key=UUID(request_key)
        )
    )
    return redirect(f"/exams/{assignment.id}")


@router.get("/exams/{exam_id}", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def exam_detail(request: Request, exam_id: int, db: Annotated[WorkspaceSession, Depends(get_db)]):
    page = _call_exam(lambda: exam_service.exam_page(db, request.state.access, exam_id))
    return templates.TemplateResponse(request, _exam_template(page), {"page": page})


@router.post("/exams/{exam_id}/submit")
@study_operation(StudyOperation.RECORD_PROGRESS)
async def submit_exam(
    request: Request, exam_id: int, db: Annotated[WorkspaceSession, Depends(get_db)]
):
    responses = _exam_responses(await request.form())
    _call_exam(lambda: exam_service.submit_exam(db, request.state.access, exam_id, responses))
    return redirect(f"/exams/{exam_id}")


@router.post("/exams/{exam_id}/release")
@study_operation(StudyOperation.MANAGE_EXAMS)
def release_exam(
    request: Request,
    exam_id: int,
    db: Annotated[WorkspaceSession, Depends(get_db)],
    note: Annotated[str, Form()] = "",
):
    _call_exam(lambda: exam_service.release_exam(db, request.state.access, exam_id, note=note))
    return redirect(f"/exams/{exam_id}")
