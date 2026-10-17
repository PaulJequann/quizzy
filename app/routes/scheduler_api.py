from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.boundary import study_operation
from app.database import get_db
from app.models import AttemptAnswer, Question
from app.schemas import QuestionIssueReportRequest, RetrievalFeedbackRequest
from app.services import concept_scheduler_service, question_health_service
from app.services.question_health_service import QuestionIssueReport
from app.study_access import StudyOperation

router = APIRouter(prefix="/api", tags=["api"])


@router.post("/attempt-answers/{answer_id}/retrieval-feedback")
@study_operation(StudyOperation.RECORD_PROGRESS)
def api_record_retrieval_feedback(
    answer_id: int,
    payload: RetrievalFeedbackRequest,
    db: Annotated[Session, Depends(get_db)],
):
    if db.get(AttemptAnswer, answer_id) is None:
        raise HTTPException(status_code=404, detail="Attempt answer not found")
    try:
        updates = concept_scheduler_service.record_retrieval_feedback(
            db,
            attempt_answer_id=answer_id,
            friction_rating=payload.retrieval_friction_rating,
            response_time_ms=payload.response_time_ms,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "attempt_answer_id": answer_id,
        "updates": [update.to_dict() for update in updates],
    }


@router.post("/questions/{question_id}/report-issue")
@study_operation(StudyOperation.MANAGE_AND_RECORD)
def api_report_question_issue(
    question_id: int,
    payload: QuestionIssueReportRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        review_item = question_health_service.report_question_issue(
            db,
            QuestionIssueReport(
                question_id=question_id,
                category=payload.category,
                note=payload.note,
                attempt_answer_id=payload.attempt_answer_id,
                reported_from=payload.reported_from,
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "review_item": review_item,
        "health_state": _require_question_health_state(db, question_id),
    }


def _require_question_health_state(db: Session, question_id: int) -> str:
    health_state = db.scalar(select(Question.health_state).where(Question.id == question_id))
    if health_state is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return health_state.value
