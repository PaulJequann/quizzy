from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.mcp.access import session_scope as _session_scope
from app.models import Attempt, AttemptRole, ConceptStatus
from app.services import concept_service, generation_service, grading_service


def list_attempts(
    actor_role: Annotated[
        AttemptRole | None,
        Field(
            description="Optional attribution filter: learner, test, or unknown. All by default."
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum attempts to return.")] = 50,
    offset: Annotated[int, Field(ge=0, description="Number of attempts to skip.")] = 0,
) -> dict[str, Any]:
    """Read workspace attempt history, including test and unknown attempts by default.

    Only learner attempts contribute to mastery. Results are newest first, with stable
    attempt ids breaking timestamp ties. Use offset to read older history.
    """
    with _session_scope() as db:
        statement = (
            select(Attempt)
            .options(selectinload(Attempt.actor))
            .order_by(Attempt.submitted_at.desc().nulls_last(), Attempt.id.desc())
        )
        if actor_role is not None:
            statement = statement.where(Attempt.actor_role == actor_role)
        attempts = db.scalars(statement.limit(limit).offset(offset))
        return {
            "attempts": [
                {
                    "id": attempt.id,
                    "workspace_id": attempt.workspace_id,
                    "quiz_id": attempt.quiz_id,
                    "exam_submission_id": attempt.exam_submission_id,
                    "actor_id": attempt.actor_id,
                    "actor_name": attempt.actor_name,
                    "actor_role": attempt.actor_role,
                    "score": attempt.score,
                    "correct_count": attempt.correct_count,
                    "total_questions": attempt.total_questions,
                    "session_type": attempt.session_type,
                    "started_at": attempt.started_at,
                    "submitted_at": attempt.submitted_at,
                }
                for attempt in attempts
            ],
            "limit": limit,
            "offset": offset,
        }


def list_concepts(
    subject_name: Annotated[
        str | None,
        Field(description="Optional subject name used to scope concepts."),
    ] = None,
    include_proposed: Annotated[
        bool,
        Field(description="Include proposed concepts that are waiting for stewardship review."),
    ] = False,
    include_archived: Annotated[
        bool,
        Field(description="Include archived concepts preserved for history."),
    ] = False,
) -> dict[str, Any]:
    """List existing active concepts, with optional review/history statuses."""
    with _session_scope() as db:
        statuses = [ConceptStatus.ACTIVE]
        if include_proposed:
            statuses.append(ConceptStatus.PROPOSED)
        if include_archived:
            statuses.append(ConceptStatus.ARCHIVED)
        return {
            "concepts": generation_service.list_concepts(
                db,
                subject_name=subject_name,
                statuses=statuses,
            )
        }


def list_concept_review_queue(
    subject_id: Annotated[
        int | None,
        Field(description="Optional subject id used to filter open concept stewardship reviews."),
    ] = None,
) -> dict[str, Any]:
    """List open concept stewardship review items."""
    with _session_scope() as db:
        return {
            "review_items": concept_service.list_open_concept_reviews(
                db,
                subject_id=subject_id,
            )
        }


def get_mastery_focus(
    subject_id: Annotated[
        int | None,
        Field(description="Optional subject id used to scope the mastery focus queue."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum number of focus concepts.")] = 10,
) -> dict[str, Any]:
    """Return weak or improving concepts using learner-only mastery evidence.

    Test and unknown attempts are excluded from mastery.
    """
    with _session_scope() as db:
        return {
            **grading_service.mastery_focus(db, subject_id=subject_id, limit=limit),
            "evidence_policy": "learner_only",
        }


def get_subject_mastery_tree(
    subject_id: Annotated[int, Field(description="Subject id to read as a mastery tree.")],
) -> dict[str, Any]:
    """Return the subject mastery tree using learner-only evidence.

    Test and unknown attempts are excluded from mastery.
    """
    with _session_scope() as db:
        return {
            **grading_service.subject_mastery_tree(db, subject_id=subject_id),
            "evidence_policy": "learner_only",
        }
