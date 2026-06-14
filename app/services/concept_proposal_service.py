from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Concept, ConceptStatus
from app.services import concept_service


def create_proposed_assignments(
    db: Session,
    request: concept_service.ProposedAssignmentRequest,
) -> list[concept_service.ResolvedConceptAssignment]:
    assignments: list[concept_service.ResolvedConceptAssignment] = []
    for proposal in concept_service._dedupe_proposals(request.proposals, request.subject.name):
        concept = concept_service._concept_by_normalized(db, request.subject, proposal.name)
        if concept is None:
            concept = _create_proposed_concept(db, request, proposal)
        assignments.append(
            concept_service.ResolvedConceptAssignment(
                concept=concept,
                role=proposal.role,
                rationale=proposal.rationale,
            )
        )
        concept_service._open_concept_review_item(
            db,
            concept_service.ReviewItemRequest(
                subject=request.subject,
                concept=concept,
                question=request.question,
                review_type=_proposed_review_type(request, proposal),
                reason=_proposed_review_reason(request, concept, proposal),
                suggested_action=request.suggested_action,
                evidence=_proposed_review_evidence(request, proposal),
            ),
        )
    return assignments


def _create_proposed_concept(
    db: Session,
    request: concept_service.ProposedAssignmentRequest,
    proposal: concept_service.ConceptProposal,
) -> Concept:
    parent = _resolve_proposed_parent(db, request, proposal)
    concept = concept_service._create_concept(
        db,
        concept_service.ConceptCreateRequest(
            subject=request.subject,
            name=proposal.name,
            parent=parent,
            status=ConceptStatus.PROPOSED,
        ),
    )
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=request.subject,
            action="concept_proposed",
            concept_ids=[concept.id],
            rationale=proposal.rationale,
            actor=request.actor,
            source=request.source,
            metadata={"parent_id": parent.id if parent else None},
        ),
    )
    return concept


def _resolve_proposed_parent(
    db: Session,
    request: concept_service.ProposedAssignmentRequest,
    proposal: concept_service.ConceptProposal,
) -> Concept | None:
    if not proposal.parent_name:
        return None
    parent = concept_service._concept_by_normalized(db, request.subject, proposal.parent_name)
    if parent is not None:
        if parent.status == ConceptStatus.ARCHIVED:
            raise ValueError(f"Parent concept '{parent.name}' is archived.")
        return parent
    return _create_proposed_parent(db, request, proposal)


def _create_proposed_parent(
    db: Session,
    request: concept_service.ProposedAssignmentRequest,
    proposal: concept_service.ConceptProposal,
) -> Concept:
    parent = concept_service._create_concept(
        db,
        concept_service.ConceptCreateRequest(
            subject=request.subject,
            name=proposal.parent_name or "",
            parent=None,
            status=ConceptStatus.PROPOSED,
        ),
    )
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=request.subject,
            action="concept_proposed",
            concept_ids=[parent.id],
            rationale=f"Proposed parent concept for {proposal.name}.",
            actor=request.actor,
            source=request.source,
        ),
    )
    concept_service._open_concept_review_item(
        db,
        concept_service.ReviewItemRequest(
            subject=request.subject,
            concept=parent,
            question=request.question,
            review_type="proposed_parent_concept",
            reason=f"Review proposed parent concept '{parent.name}' for '{proposal.name}'.",
            suggested_action="approve_place_or_reject_concept",
            evidence=_parent_review_evidence(request, proposal),
        ),
    )
    return parent


def _parent_review_evidence(
    request: concept_service.ProposedAssignmentRequest,
    proposal: concept_service.ConceptProposal,
) -> dict[str, Any]:
    return {
        "child_proposal": concept_service._proposal_to_dict(proposal),
        **(request.review_evidence or {}),
    }


def _proposed_review_type(
    request: concept_service.ProposedAssignmentRequest,
    proposal: concept_service.ConceptProposal,
) -> str:
    if request.suggested_action == "approve_place_or_reject_concept" and not proposal.parent_name:
        return "proposed_concept_placement"
    return request.review_type


def _proposed_review_reason(
    request: concept_service.ProposedAssignmentRequest,
    concept: Concept,
    proposal: concept_service.ConceptProposal,
) -> str:
    if request.review_reason.strip():
        return request.review_reason.strip()
    if request.suggested_action == "approve_place_or_reject_concept" and not proposal.parent_name:
        return (
            f"Review parent placement for proposed concept '{concept.name}' before approval. "
            "Move it under a durable parent when this is source-specific taxonomy."
        )
    return f"Question depends on unresolved proposed concept '{concept.name}'."


def _proposed_review_evidence(
    request: concept_service.ProposedAssignmentRequest,
    proposal: concept_service.ConceptProposal,
) -> dict[str, Any]:
    return {
        "proposal": concept_service._proposal_to_dict(proposal),
        "placement_review_required": proposal.parent_name is None,
        **(request.review_evidence or {}),
    }
