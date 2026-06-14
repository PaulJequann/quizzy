from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Any

from pydantic import Field

from app.database import get_session_factory
from app.models import ConceptStatus
from app.services import concept_service, generation_service, grading_service


@contextmanager
def _session_scope():
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
    """Return weak or improving concepts that need attention."""
    with _session_scope() as db:
        return grading_service.mastery_focus(db, subject_id=subject_id, limit=limit)


def get_subject_mastery_tree(
    subject_id: Annotated[int, Field(description="Subject id to read as a mastery tree.")],
) -> dict[str, Any]:
    """Return the full hierarchy-aware mastery tree for one subject."""
    with _session_scope() as db:
        return grading_service.subject_mastery_tree(db, subject_id=subject_id)
