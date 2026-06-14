from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Concept
from app.schemas import ConceptArchiveRequest
from app.services import concept_lifecycle_service


def archive_concept(
    db: Session,
    concept: Concept,
    payload: ConceptArchiveRequest,
) -> Concept:
    return concept_lifecycle_service.archive_concept(
        db,
        concept_lifecycle_service.ArchiveConceptRequest(
            concept=concept,
            archive_children=payload.archive_children,
            rationale=payload.rationale,
        ),
    )
