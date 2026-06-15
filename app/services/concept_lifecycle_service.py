from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Concept,
    ConceptAlias,
    ConceptAssignmentRole,
    ConceptDecisionEvent,
    ConceptReviewItem,
    ConceptStatus,
    QuestionConcept,
    QuestionStatus,
    Subject,
)
from app.services import concept_service
from app.services.text_service import slugify


@dataclass(frozen=True)
class SplitConceptRequest:
    concept: Concept
    child_names: list[str]
    question_ids_by_child_name: dict[str, list[int]]
    rationale: str = ""


@dataclass(frozen=True)
class ArchiveConceptRequest:
    concept: Concept
    archive_children: bool
    rationale: str = ""


@dataclass(frozen=True)
class CreateConceptRequest:
    subject: Subject
    name: str
    parent: Concept | None = None
    status: ConceptStatus = ConceptStatus.ACTIVE
    rationale: str = ""


def create_concept(db: Session, request: CreateConceptRequest) -> Concept:
    if request.status == ConceptStatus.ARCHIVED:
        raise ValueError("New concepts cannot be created as archived.")
    _validate_new_parent(request.subject.id, request.parent)
    if request.status == ConceptStatus.ACTIVE and _parent_is_not_active(request.parent):
        raise ValueError("Active concepts require an active parent.")
    concept = concept_service._create_concept(
        db,
        concept_service.ConceptCreateRequest(
            subject=request.subject,
            name=request.name,
            parent=request.parent,
            status=request.status,
        ),
    )
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=request.subject,
            action="concept_creation",
            concept_ids=[concept.id],
            rationale=request.rationale,
            metadata={"parent_id": request.parent.id if request.parent else None},
        ),
    )
    db.commit()
    db.refresh(concept)
    return concept


def rename_concept(db: Session, concept: Concept, *, name: str, rationale: str = "") -> Concept:
    old_name = concept.name
    concept_service._assert_normalized_name_available(
        db,
        concept.subject,
        name,
        ignore_concept_id=concept.id,
    )
    concept.name = name.strip()
    concept.slug = slugify(concept.name)
    concept.normalized_name = concept_service.normalize_concept_name(
        concept.name,
        subject_name=concept.subject.name,
    )
    _create_alias_if_available(db, alias_name=old_name, target=concept)
    concept_service._record_decision(db, _rename_decision(concept, old_name, rationale))
    db.commit()
    db.refresh(concept)
    return concept


def move_concept(
    db: Session,
    concept: Concept,
    *,
    parent: Concept | None,
    rationale: str = "",
) -> Concept:
    _validate_parent_move(concept, parent)
    concept.parent = parent
    concept_service._record_decision(db, _move_decision(concept, parent, rationale))
    db.commit()
    db.refresh(concept)
    return concept


def split_concept(db: Session, request: SplitConceptRequest) -> dict[str, Any]:
    children = [
        concept_service._get_or_create_active_concept(
            db,
            request.concept.subject,
            name,
            parent=request.concept,
        )
        for name in request.child_names
    ]
    reassigned = _reassign_split_questions(db, request, children)
    concept_service._record_decision(db, _split_decision(request, children))
    db.commit()
    return {
        "parent": concept_service._concept_to_dict(request.concept),
        "children": [concept_service._concept_to_dict(child) for child in children],
        "reassigned_question_count": reassigned,
    }


def archive_concept(db: Session, request: ArchiveConceptRequest) -> Concept:
    request.concept.status = ConceptStatus.ARCHIVED
    archived_children = _archive_children(request.concept) if request.archive_children else []
    concept_service._record_decision(db, _archive_decision(request, archived_children))
    db.commit()
    db.refresh(request.concept)
    return request.concept


def delete_empty_concept(db: Session, concept: Concept) -> None:
    if _cannot_delete(concept) or _has_concept_history(db, concept):
        raise ValueError("Only empty, history-free concepts can be deleted.")
    db.delete(concept)
    db.commit()


def approve_proposed_concept(db: Session, concept: Concept, *, rationale: str = "") -> Concept:
    if concept.status != ConceptStatus.PROPOSED:
        raise ValueError("Only proposed concepts can be approved.")
    if concept.parent is not None and concept.parent.status != ConceptStatus.ACTIVE:
        raise ValueError("Approve or move the parent concept before approving this concept.")
    concept.status = ConceptStatus.ACTIVE
    activated_question_ids = _activate_resolved_draft_questions(concept)
    _close_concept_reviews(db, concept, resolution=rationale or "approved")
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=concept.subject,
            action="proposed_concept_approval",
            concept_ids=[concept.id],
            question_ids=activated_question_ids,
            rationale=rationale,
        ),
    )
    db.commit()
    db.refresh(concept)
    return concept


def forget_alias(db: Session, alias: ConceptAlias, *, rationale: str = "") -> ConceptAlias:
    alias.is_active = False
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=alias.target_concept.subject,
            action="alias_forget",
            concept_ids=[alias.target_concept_id],
            rationale=rationale,
            metadata={"alias": alias.alias_name},
        ),
    )
    db.commit()
    db.refresh(alias)
    return alias


def reject_proposed_concept(db: Session, concept: Concept, *, rationale: str = "") -> Concept:
    if concept.status != ConceptStatus.PROPOSED:
        raise ValueError("Only proposed concepts can be rejected.")
    affected_question_ids = [link.question_id for link in concept.question_links]
    concept.status = ConceptStatus.ARCHIVED
    for question in concept.questions:
        question.status = QuestionStatus.DRAFT
    _close_concept_reviews(db, concept, resolution=rationale or "rejected")
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=concept.subject,
            action="proposed_concept_rejection",
            concept_ids=[concept.id],
            question_ids=affected_question_ids,
            rationale=rationale,
        ),
    )
    db.commit()
    db.refresh(concept)
    return concept


def accept_review_assignment(
    db: Session,
    item: ConceptReviewItem,
    *,
    rationale: str = "",
) -> ConceptReviewItem:
    _validate_open_question_review(item)
    activated_question_ids = _activate_question_if_resolved(item)
    _resolve_review_item(item, rationale or "accepted current assignment")
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=item.subject,
            action="concept_review_assignment_acceptance",
            concept_ids=[item.concept_id] if item.concept_id is not None else [],
            question_ids=[item.question_id] if item.question_id is not None else [],
            rationale=rationale,
            metadata={"activated_question_ids": activated_question_ids},
        ),
    )
    db.commit()
    db.refresh(item)
    return item


def remap_review_assignment(
    db: Session,
    item: ConceptReviewItem,
    *,
    target: Concept,
    rationale: str = "",
) -> ConceptReviewItem:
    _validate_open_question_review(item)
    question_id = _review_question_id(item)
    source = _review_source_concept(item)
    _validate_review_target(item, target)
    _remap_question_link(db, question_id, source=source, target=target)
    activated_question_ids = _activate_question_if_resolved(item)
    _resolve_review_item(item, rationale or f"remapped to {target.name}")
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=item.subject,
            action="concept_review_assignment_remap",
            concept_ids=[source.id, target.id],
            question_ids=[question_id],
            rationale=rationale,
            metadata={
                "target_concept_id": target.id,
                "activated_question_ids": activated_question_ids,
            },
        ),
    )
    db.commit()
    db.refresh(item)
    return item


def _review_question_id(item: ConceptReviewItem) -> int:
    if item.question_id is None:
        raise ValueError("Review item has no question to remediate.")
    return item.question_id


def _review_source_concept(item: ConceptReviewItem) -> Concept:
    if item.concept is None:
        raise ValueError("Review item has no source concept.")
    return item.concept


def _validate_review_target(item: ConceptReviewItem, target: Concept) -> None:
    if target.subject_id != item.subject_id:
        raise ValueError("Target concept must belong to the same subject.")
    if target.status == ConceptStatus.ARCHIVED:
        raise ValueError("Archived concepts cannot receive review assignments.")


def _validate_new_parent(subject_id: int, parent: Concept | None) -> None:
    if parent is None:
        return
    if parent.subject_id != subject_id:
        raise ValueError("Parent concept must belong to the same subject.")
    if parent.status == ConceptStatus.ARCHIVED:
        raise ValueError("Archived concepts cannot be used as parents.")


def _parent_is_not_active(parent: Concept | None) -> bool:
    return parent is not None and parent.status != ConceptStatus.ACTIVE


def _remap_question_link(
    db: Session,
    question_id: int,
    *,
    source: Concept,
    target: Concept,
) -> None:
    if target.id == source.id:
        return
    if not _reassign_question_to_concept(db, question_id, source=source, target=target):
        raise ValueError("Review item source assignment is no longer linked to the question.")


def _activate_resolved_draft_questions(concept: Concept) -> list[int]:
    activated: list[int] = []
    for question in concept.questions:
        if question.status == QuestionStatus.DRAFT and _question_concepts_are_active(question):
            question.status = QuestionStatus.ACTIVE
            activated.append(question.id)
    return activated


def _activate_question_if_resolved(item: ConceptReviewItem) -> list[int]:
    if item.question is None or item.question.status != QuestionStatus.DRAFT:
        return []
    if not _question_concepts_are_active(item.question):
        return []
    item.question.status = QuestionStatus.ACTIVE
    return [item.question.id]


def _question_concepts_are_active(question) -> bool:
    return all(link.concept.status == ConceptStatus.ACTIVE for link in question.concept_links)


def _validate_open_question_review(item: ConceptReviewItem) -> None:
    if item.status != "open":
        raise ValueError("Only open review items can be resolved.")
    if item.question is None or item.question_id is None:
        raise ValueError("Review item has no question to remediate.")


def _resolve_review_item(item: ConceptReviewItem, resolution: str) -> None:
    item.status = "resolved"
    item.resolution_notes = resolution


def _close_concept_reviews(db: Session, concept: Concept, *, resolution: str) -> None:
    for item in db.scalars(
        select(ConceptReviewItem).where(
            ConceptReviewItem.concept_id == concept.id,
            ConceptReviewItem.status == "open",
        )
    ):
        _resolve_review_item(item, resolution)


def _rename_decision(
    concept: Concept,
    old_name: str,
    rationale: str,
) -> concept_service.DecisionRecord:
    return concept_service.DecisionRecord(
        subject=concept.subject,
        action="rename",
        concept_ids=[concept.id],
        rationale=rationale,
        metadata={"old_name": old_name, "new_name": concept.name},
    )


def _move_decision(
    concept: Concept,
    parent: Concept | None,
    rationale: str,
) -> concept_service.DecisionRecord:
    return concept_service.DecisionRecord(
        subject=concept.subject,
        action="move",
        concept_ids=[concept.id, parent.id] if parent else [concept.id],
        rationale=rationale,
        metadata={"parent_id": parent.id if parent else None},
    )


def _split_decision(
    request: SplitConceptRequest,
    children: list[Concept],
) -> concept_service.DecisionRecord:
    return concept_service.DecisionRecord(
        subject=request.concept.subject,
        action="split",
        concept_ids=[request.concept.id, *[child.id for child in children]],
        question_ids=_split_question_ids(request.question_ids_by_child_name),
        rationale=request.rationale,
    )


def _archive_decision(
    request: ArchiveConceptRequest,
    archived_children: list[int],
) -> concept_service.DecisionRecord:
    return concept_service.DecisionRecord(
        subject=request.concept.subject,
        action="archive",
        concept_ids=[request.concept.id, *archived_children],
        rationale=request.rationale,
        metadata={"archive_children": request.archive_children},
    )


def _create_alias_if_available(
    db: Session,
    *,
    alias_name: str,
    target: Concept,
) -> ConceptAlias | None:
    normalized = concept_service.normalize_concept_name(
        alias_name,
        subject_name=target.subject.name,
    )
    if target.normalized_name == normalized:
        return None
    existing_alias = db.scalar(
        select(ConceptAlias).where(
            ConceptAlias.subject_id == target.subject_id,
            ConceptAlias.alias_normalized_name == normalized,
        )
    )
    if existing_alias is not None:
        existing_alias.target_concept = target
        existing_alias.is_active = True
        return existing_alias
    alias = ConceptAlias(
        subject_id=target.subject_id,
        alias_name=alias_name,
        alias_normalized_name=normalized,
        target_concept=target,
        is_active=True,
    )
    db.add(alias)
    return alias


def _reassign_split_questions(
    db: Session,
    request: SplitConceptRequest,
    children: list[Concept],
) -> int:
    return sum(
        _reassign_questions_to_concept(
            db,
            request.question_ids_by_child_name.get(child.name, []),
            source=request.concept,
            target=child,
        )
        for child in children
    )


def _reassign_questions_to_concept(
    db: Session,
    question_ids: list[int],
    *,
    source: Concept,
    target: Concept,
) -> int:
    return sum(
        _reassign_question_to_concept(db, question_id, source=source, target=target)
        for question_id in question_ids
    )


def _reassign_question_to_concept(
    db: Session,
    question_id: int,
    *,
    source: Concept,
    target: Concept,
) -> int:
    link = db.get(QuestionConcept, {"question_id": question_id, "concept_id": source.id})
    if link is None:
        return 0
    existing = db.get(QuestionConcept, {"question_id": question_id, "concept_id": target.id})
    if existing is None:
        link.concept = target
    else:
        _dedupe_target_link(existing, link)
        db.delete(link)
    return 1


def _dedupe_target_link(target_link: QuestionConcept, source_link: QuestionConcept) -> None:
    if source_link.role == ConceptAssignmentRole.PRIMARY:
        target_link.role = ConceptAssignmentRole.PRIMARY


def _split_question_ids(question_ids_by_child_name: dict[str, list[int]]) -> list[int]:
    return [
        question_id
        for question_ids in question_ids_by_child_name.values()
        for question_id in question_ids
    ]


def _archive_children(concept: Concept) -> list[int]:
    archived_ids: list[int] = []
    for child in concept.children:
        child.status = ConceptStatus.ARCHIVED
        archived_ids.append(child.id)
    return archived_ids


def _cannot_delete(concept: Concept) -> bool:
    return bool(concept.question_links or concept.children)


def _validate_parent_move(concept: Concept, parent: Concept | None) -> None:
    _validate_parent_subject(concept, parent)
    _validate_parent_status(concept, parent)
    _validate_parent_cycle(concept, parent)


def _validate_parent_subject(concept: Concept, parent: Concept | None) -> None:
    if parent is None:
        return
    if parent.subject_id != concept.subject_id:
        raise ValueError("A concept parent must belong to the same subject.")


def _validate_parent_status(concept: Concept, parent: Concept | None) -> None:
    if parent is None:
        return
    if parent.status == ConceptStatus.ARCHIVED:
        raise ValueError("Archived concepts cannot be used as parents.")
    if concept.status == ConceptStatus.ACTIVE and _parent_is_not_active(parent):
        raise ValueError("Active concepts require an active parent.")


def _validate_parent_cycle(concept: Concept, parent: Concept | None) -> None:
    if parent is None:
        return
    if _would_create_cycle(concept, parent):
        raise ValueError("Concept hierarchy cannot contain cycles.")


def _would_create_cycle(concept: Concept, parent: Concept) -> bool:
    current: Concept | None = parent
    while current is not None:
        if current.id == concept.id:
            return True
        current = current.parent
    return False


def _has_concept_history(db: Session, concept: Concept) -> bool:
    rows = db.scalars(
        select(ConceptDecisionEvent.affected_concept_ids_json).where(
            ConceptDecisionEvent.subject_id == concept.subject_id,
        )
    )
    return any(_json_ids_include(raw_ids, concept.id) for raw_ids in rows)


def _json_ids_include(raw_ids: str, concept_id: int) -> bool:
    try:
        ids = json.loads(raw_ids)
    except json.JSONDecodeError:
        return False
    return isinstance(ids, list) and concept_id in ids
