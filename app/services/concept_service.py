from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Concept,
    ConceptAlias,
    ConceptAssignmentRole,
    ConceptDecisionEvent,
    ConceptReviewItem,
    ConceptStatus,
    Question,
    QuestionConcept,
    QuestionStatus,
    Subject,
)
from app.services.text_service import slugify


@dataclass(frozen=True)
class ConceptProposal:
    name: str
    parent_name: str | None = None
    role: ConceptAssignmentRole = ConceptAssignmentRole.PRIMARY
    rationale: str = ""


@dataclass(frozen=True)
class ResolvedConceptAssignment:
    concept: Concept
    role: ConceptAssignmentRole
    rationale: str


@dataclass(frozen=True)
class AssignmentResolutionRequest:
    subject: Subject
    proposals: list[ConceptProposal]
    allow_new_active: bool
    actor: str = "app"
    source: str = "web"


@dataclass(frozen=True)
class ProposedAssignmentRequest:
    subject: Subject
    proposals: list[ConceptProposal]
    question: Question
    actor: str = "mcp_agent"
    source: str = "mcp"
    review_type: str = "unresolved_assignment"
    review_reason: str = ""
    suggested_action: str = "approve_or_reject_concept"
    review_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConceptCreateRequest:
    subject: Subject
    name: str
    parent: Concept | None
    status: ConceptStatus


@dataclass(frozen=True)
class DecisionRecord:
    subject: Subject
    action: str
    concept_ids: list[int]
    question_ids: list[int] | None = None
    rationale: str = ""
    actor: str = "app"
    source: str = "web"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class QuestionAssignmentDecision:
    subject: Subject
    question: Question
    assignments: list[ResolvedConceptAssignment]
    actor: str
    source: str


@dataclass(frozen=True)
class ReviewItemRequest:
    subject: Subject
    concept: Concept | None
    question: Question | None
    review_type: str
    reason: str
    suggested_action: str
    evidence: dict[str, Any]


def normalize_concept_name(name: str, *, subject_name: str = "") -> str:
    normalized = slugify(name)
    subject_prefix = slugify(subject_name)
    if subject_prefix and normalized.startswith(f"{subject_prefix}-"):
        normalized = normalized[len(subject_prefix) + 1 :]
    tokens = [_singular_token(token) for token in normalized.split("-") if token]
    return "-".join(tokens) or "untitled"


def concept_proposals_from_names(names: list[str]) -> list[ConceptProposal]:
    proposals: list[ConceptProposal] = []
    seen: set[str] = set()
    for index, raw_name in enumerate(names):
        name = raw_name.strip()
        normalized = normalize_concept_name(name)
        if not name or normalized in seen:
            continue
        seen.add(normalized)
        proposals.append(
            ConceptProposal(
                name=name,
                role=(
                    ConceptAssignmentRole.PRIMARY if index == 0 else ConceptAssignmentRole.SECONDARY
                ),
            )
        )
    return proposals


def resolve_concept_assignments(
    db: Session,
    request: AssignmentResolutionRequest,
) -> list[ResolvedConceptAssignment]:
    normalized = _dedupe_proposals(request.proposals, request.subject.name)
    _validate_assignment_roles(normalized)
    assignments = [_resolve_one_assignment(db, request, proposal) for proposal in normalized]
    _ensure_assignable_concepts(assignments)
    return assignments


def apply_question_assignments(
    question: Question,
    assignments: list[ResolvedConceptAssignment],
) -> None:
    question.concept_links = [
        QuestionConcept(
            concept=assignment.concept,
            role=assignment.role,
            rationale=assignment.rationale,
        )
        for assignment in assignments
    ]
    if question.status == QuestionStatus.ACTIVE:
        _validate_active_question_assignments(question)


def list_open_concept_reviews(
    db: Session,
    *,
    subject_id: int | None = None,
) -> list[dict[str, Any]]:
    statement = (
        select(ConceptReviewItem)
        .where(ConceptReviewItem.status == "open")
        .options(
            selectinload(ConceptReviewItem.concept),
            selectinload(ConceptReviewItem.question),
            selectinload(ConceptReviewItem.subject),
        )
        .order_by(ConceptReviewItem.created_at, ConceptReviewItem.id)
    )
    if subject_id is not None:
        statement = statement.where(ConceptReviewItem.subject_id == subject_id)
    return [_review_item_to_dict(item) for item in db.scalars(statement)]


def _singular_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if _has_plural_es_suffix(token):
        return token[:-2]
    if _has_simple_plural_suffix(token):
        return token[:-1]
    return token


def _has_plural_es_suffix(token: str) -> bool:
    return len(token) > 4 and token.endswith(("ches", "shes", "sses", "xes", "zes"))


def _has_simple_plural_suffix(token: str) -> bool:
    return len(token) > 3 and token.endswith("s") and not token.endswith(("ias", "ss", "us"))


def _dedupe_proposals(
    proposals: list[ConceptProposal],
    subject_name: str,
) -> list[ConceptProposal]:
    deduped: list[ConceptProposal] = []
    seen: set[str] = set()
    for proposal in proposals:
        name = proposal.name.strip()
        key = normalize_concept_name(name, subject_name=subject_name)
        if name and key not in seen:
            deduped.append(proposal)
            seen.add(key)
    return deduped


def _validate_assignment_roles(proposals: list[ConceptProposal]) -> None:
    primary_count = sum(proposal.role == ConceptAssignmentRole.PRIMARY for proposal in proposals)
    if primary_count != 1:
        raise ValueError("Questions require exactly one primary concept assignment.")
    if len(proposals) > 2:
        raise ValueError("Questions can tag at most two concepts.")


def _resolve_one_assignment(
    db: Session,
    request: AssignmentResolutionRequest,
    proposal: ConceptProposal,
) -> ResolvedConceptAssignment:
    concept = _existing_assignment_concept(db, request.subject, proposal)
    if concept is None:
        concept = _create_active_assignment_concept(db, request, proposal)
    if concept.status != ConceptStatus.ACTIVE:
        raise ValueError(f"Concept '{concept.name}' is not active.")
    return ResolvedConceptAssignment(
        concept=concept,
        role=proposal.role,
        rationale=proposal.rationale,
    )


def _existing_assignment_concept(
    db: Session,
    subject: Subject,
    proposal: ConceptProposal,
) -> Concept | None:
    concept = _concept_by_normalized(db, subject, proposal.name)
    if concept is not None:
        return concept
    alias = _alias_by_normalized(db, subject, proposal.name)
    return alias.target_concept if alias and alias.is_active else None


def _create_active_assignment_concept(
    db: Session,
    request: AssignmentResolutionRequest,
    proposal: ConceptProposal,
) -> Concept:
    _validate_new_active_proposal(request, proposal)
    parent = _resolve_parent(db, request, proposal)
    concept = _create_concept(
        db,
        ConceptCreateRequest(
            subject=request.subject,
            name=proposal.name,
            parent=parent,
            status=ConceptStatus.ACTIVE,
        ),
    )
    _record_decision(
        db,
        DecisionRecord(
            subject=request.subject,
            action="concept_creation",
            concept_ids=[concept.id],
            rationale=proposal.rationale,
            actor=request.actor,
            source=request.source,
            metadata={"parent_id": parent.id if parent else None},
        ),
    )
    return concept


def _validate_new_active_proposal(
    request: AssignmentResolutionRequest,
    proposal: ConceptProposal,
) -> None:
    if not request.allow_new_active:
        raise ValueError("New active concepts require approved concept plan.")
    if request.source == "mcp" and not proposal.rationale.strip():
        raise ValueError("New concept proposals require a rationale.")


def _resolve_parent(
    db: Session,
    request: AssignmentResolutionRequest,
    proposal: ConceptProposal,
) -> Concept | None:
    if not proposal.parent_name:
        return None
    parent = _concept_by_normalized(db, request.subject, proposal.parent_name)
    if parent is not None:
        if parent.status == ConceptStatus.ACTIVE:
            return parent
        raise ValueError(f"Parent concept '{parent.name}' is not active.")
    parent = _create_concept(
        db,
        ConceptCreateRequest(
            subject=request.subject,
            name=proposal.parent_name,
            parent=None,
            status=ConceptStatus.ACTIVE,
        ),
    )
    _record_decision(
        db,
        DecisionRecord(
            subject=request.subject,
            action="concept_creation",
            concept_ids=[parent.id],
            rationale=f"Created parent concept for {proposal.name}.",
            actor=request.actor,
            source=request.source,
        ),
    )
    return parent


def _create_concept(
    db: Session,
    request: ConceptCreateRequest,
) -> Concept:
    _assert_normalized_name_available(db, request.subject, request.name)
    concept = Concept(
        subject=request.subject,
        name=request.name.strip(),
        slug=slugify(request.name),
        normalized_name=normalize_concept_name(
            request.name,
            subject_name=request.subject.name,
        ),
        parent=request.parent,
        status=request.status,
    )
    db.add(concept)
    db.flush()
    return concept


def _get_or_create_active_concept(
    db: Session,
    subject: Subject,
    name: str,
    *,
    parent: Concept | None,
) -> Concept:
    existing = _concept_by_normalized(db, subject, name)
    if existing:
        if existing.status != ConceptStatus.ACTIVE:
            raise ValueError(f"Concept '{existing.name}' is not active.")
        return existing
    return _create_concept(
        db,
        ConceptCreateRequest(
            subject=subject,
            name=name,
            parent=parent,
            status=ConceptStatus.ACTIVE,
        ),
    )


def _assert_normalized_name_available(
    db: Session,
    subject: Subject,
    name: str,
    *,
    ignore_concept_id: int | None = None,
) -> None:
    normalized = normalize_concept_name(name, subject_name=subject.name)
    concept = db.scalar(
        select(Concept).where(
            Concept.subject_id == subject.id,
            Concept.normalized_name == normalized,
        )
    )
    if concept is not None and concept.id != ignore_concept_id:
        raise ValueError(f"Concept '{name}' already exists in {subject.name}.")
    alias = db.scalar(
        select(ConceptAlias).where(
            ConceptAlias.subject_id == subject.id,
            ConceptAlias.alias_normalized_name == normalized,
            ConceptAlias.is_active.is_(True),
        )
    )
    if alias is not None:
        raise ValueError(f"Concept name '{name}' is already used as an active alias.")


def _ensure_assignable_concepts(assignments: list[ResolvedConceptAssignment]) -> None:
    archived = [
        assignment.concept.name
        for assignment in assignments
        if assignment.concept.status == ConceptStatus.ARCHIVED
    ]
    if archived:
        raise ValueError(f"Cannot assign archived concepts: {', '.join(archived)}")


def _validate_active_question_assignments(question: Question) -> None:
    primary_count = sum(
        link.role == ConceptAssignmentRole.PRIMARY for link in question.concept_links
    )
    if primary_count != 1:
        raise ValueError("Active questions require exactly one primary concept assignment.")
    unresolved = [
        link.concept.name
        for link in question.concept_links
        if link.concept.status != ConceptStatus.ACTIVE
    ]
    if unresolved:
        raise ValueError("Active questions cannot depend on unresolved proposed concepts.")


def _get_subject_by_name(db: Session, subject_name: str) -> Subject | None:
    return db.scalar(select(Subject).where(Subject.slug == slugify(subject_name)))


def _concept_by_normalized(db: Session, subject: Subject, name: str) -> Concept | None:
    return db.scalar(
        select(Concept).where(
            Concept.subject_id == subject.id,
            Concept.normalized_name == normalize_concept_name(name, subject_name=subject.name),
        )
    )


def _alias_by_normalized(db: Session, subject: Subject, name: str) -> ConceptAlias | None:
    return db.scalar(
        select(ConceptAlias)
        .where(
            ConceptAlias.subject_id == subject.id,
            ConceptAlias.alias_normalized_name
            == normalize_concept_name(
                name,
                subject_name=subject.name,
            ),
            ConceptAlias.is_active.is_(True),
        )
        .options(selectinload(ConceptAlias.target_concept))
    )


def _record_decision(
    db: Session,
    record: DecisionRecord,
) -> ConceptDecisionEvent:
    event = ConceptDecisionEvent(
        subject_id=record.subject.id,
        action=record.action,
        affected_concept_ids_json=json.dumps(record.concept_ids, sort_keys=True),
        affected_question_ids_json=json.dumps(record.question_ids or [], sort_keys=True),
        rationale=record.rationale,
        actor=record.actor,
        source=record.source,
        metadata_json=json.dumps(record.metadata or {}, sort_keys=True),
    )
    db.add(event)
    db.flush()
    return event


def record_question_assignment_decision(
    db: Session,
    decision: QuestionAssignmentDecision,
) -> None:
    _record_decision(
        db,
        DecisionRecord(
            subject=decision.subject,
            action="question_assignment",
            concept_ids=[assignment.concept.id for assignment in decision.assignments],
            question_ids=[decision.question.id],
            rationale="Assigned question concepts from structured proposals.",
            actor=decision.actor,
            source=decision.source,
            metadata={"assignments": _assignment_metadata(decision.assignments)},
        ),
    )


def _assignment_metadata(
    assignments: list[ResolvedConceptAssignment],
) -> list[dict[str, Any]]:
    return [
        {
            "concept_id": assignment.concept.id,
            "role": assignment.role.value,
            "rationale": assignment.rationale,
        }
        for assignment in assignments
    ]


def _open_concept_review_item(
    db: Session,
    request: ReviewItemRequest,
) -> ConceptReviewItem:
    item = ConceptReviewItem(
        subject_id=request.subject.id,
        concept=request.concept,
        question=request.question,
        review_type=request.review_type,
        reason=request.reason,
        suggested_action=request.suggested_action,
        evidence_json=json.dumps(request.evidence, sort_keys=True),
        status="open",
        resolution_notes="",
    )
    db.add(item)
    return item


def _proposal_to_dict(proposal: ConceptProposal) -> dict[str, Any]:
    return {
        "name": proposal.name,
        "normalized_name": normalize_concept_name(proposal.name),
        "parent_name": proposal.parent_name,
        "role": proposal.role.value,
        "rationale": proposal.rationale,
    }


def _review_item_to_dict(item: ConceptReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "subject_id": item.subject_id,
        "subject_name": item.subject.name,
        "concept_id": item.concept_id,
        "concept_name": item.concept.name if item.concept else None,
        "concept_status": item.concept.status.value if item.concept else None,
        "question_id": item.question_id,
        "question_stem": item.question.stem if item.question else None,
        "review_type": item.review_type,
        "reason": item.reason,
        "suggested_action": item.suggested_action,
        "evidence": json.loads(item.evidence_json or "{}"),
        "status": item.status,
    }


def _subject_to_dict(
    subject: Subject | None,
    *,
    requested_name: str | None = None,
) -> dict[str, Any]:
    if subject is None:
        return {
            "id": None,
            "name": requested_name,
            "slug": slugify(requested_name or "") if requested_name else None,
            "exists": False,
        }
    return {
        "id": subject.id,
        "name": subject.name,
        "slug": subject.slug,
        "description": subject.description,
        "exists": True,
    }


def _concept_to_dict(concept: Concept) -> dict[str, Any]:
    return {
        "id": concept.id,
        "name": concept.name,
        "slug": concept.slug,
        "normalized_name": concept.normalized_name,
        "subject_id": concept.subject_id,
        "parent_concept_id": concept.parent_concept_id,
        "status": concept.status.value,
    }
