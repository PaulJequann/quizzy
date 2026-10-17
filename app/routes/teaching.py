import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.boundary import request_access, study_operation
from app.database import get_db
from app.models import Attempt
from app.services.teaching_brief_service import prepare_next_practice
from app.study_access import StudyOperation
from app.templating import templates

router = APIRouter(dependencies=[Depends(request_access)])


@router.get("/teaching")
@study_operation(StudyOperation.MANAGE)
def teaching_page(
    request: Request, db: Annotated[Session, Depends(get_db)], subject_id: int | None = None
):
    brief = prepare_next_practice(db, request.state.access.workspace_id, subject_id)
    return templates.TemplateResponse(
        request, "accounts/teaching.html", {"brief": json.dumps(brief, indent=2)}
    )


@router.get("/progress")
@study_operation(StudyOperation.VIEW)
def progress_page(request: Request, db: Annotated[Session, Depends(get_db)]):
    attempts = list(
        db.scalars(
            select(Attempt)
            .options(selectinload(Attempt.quiz))
            .where(Attempt.exam_submission_id.is_(None))
            .order_by(Attempt.submitted_at.desc(), Attempt.id.desc())
            .limit(100)
        )
    )
    return templates.TemplateResponse(request, "accounts/progress.html", {"attempts": attempts})
