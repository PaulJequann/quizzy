from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import ConceptStatus, Subject
from app.services import concept_service


def prepare_concept_plan(
    db: Session,
    *,
    subject_name: str,
    proposals: list[concept_service.ConceptProposal],
) -> dict[str, Any]:
    subject = concept_service._get_subject_by_name(db, subject_name)
    entries = [
        _proposal_plan_entry(db, subject, proposal)
        for proposal in concept_service._dedupe_proposals(proposals, subject_name)
    ]
    return {
        "subject": concept_service._subject_to_dict(subject, requested_name=subject_name),
        "requires_approval": any(entry["resolution"] == "new" for entry in entries),
        "entries": entries,
        "warnings": _near_duplicate_warnings(subject, entries),
    }


def _proposal_plan_entry(
    db: Session,
    subject: Subject | None,
    proposal: concept_service.ConceptProposal,
) -> dict[str, Any]:
    if subject is None:
        return _new_plan_entry(proposal) | {"placement_review_required": True}
    concept = concept_service._concept_by_normalized(db, subject, proposal.name)
    alias = concept_service._alias_by_normalized(db, subject, proposal.name)
    parent = _parent_plan(db, subject, proposal)
    resolution, resolved_concept = _proposal_resolution(concept, alias)
    return concept_service._proposal_to_dict(proposal) | {
        "resolution": resolution,
        "concept": resolved_concept,
        "parent": parent,
        "placement_review_required": resolution == "new" and parent is None,
    }


def _new_plan_entry(proposal: concept_service.ConceptProposal) -> dict[str, Any]:
    return concept_service._proposal_to_dict(proposal) | {
        "resolution": "new",
        "concept": None,
        "parent": None,
    }


def _proposal_resolution(concept, alias) -> tuple[str, dict[str, Any] | None]:
    if concept and concept.status == ConceptStatus.ACTIVE:
        return "existing", concept_service._concept_to_dict(concept)
    if alias and alias.target_concept.status == ConceptStatus.ACTIVE:
        return "alias", concept_service._concept_to_dict(alias.target_concept)
    return "new", None


def _parent_plan(
    db: Session,
    subject: Subject,
    proposal: concept_service.ConceptProposal,
) -> dict[str, Any] | None:
    if not proposal.parent_name:
        return None
    parent = concept_service._concept_by_normalized(db, subject, proposal.parent_name)
    if parent and parent.status in {ConceptStatus.ACTIVE, ConceptStatus.PROPOSED}:
        return {
            "resolution": "existing" if parent.status == ConceptStatus.ACTIVE else "proposed",
            "concept": concept_service._concept_to_dict(parent),
        }
    return {
        "resolution": "new",
        "name": proposal.parent_name,
        "normalized_name": concept_service.normalize_concept_name(
            proposal.parent_name,
            subject_name=subject.name,
        ),
    }


def _near_duplicate_warnings(
    subject: Subject | None,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if subject is None:
        return []
    active = [concept for concept in subject.concepts if concept.status == ConceptStatus.ACTIVE]
    return [_warning for entry in entries if (_warning := _near_duplicate_warning(active, entry))]


def _near_duplicate_warning(active, entry: dict[str, Any]) -> dict[str, Any] | None:
    if entry["resolution"] != "new":
        return None
    proposal_tokens = set(entry["normalized_name"].split("-"))
    matches = [
        concept_service._concept_to_dict(concept)
        for concept in active
        if proposal_tokens and proposal_tokens & set(concept.normalized_name.split("-"))
    ]
    return {"proposal": entry["name"], "near_matches": matches} if matches else None
