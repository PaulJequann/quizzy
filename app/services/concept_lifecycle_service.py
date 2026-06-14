from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Concept,
    ConceptAlias,
    ConceptAssignmentRole,
    ConceptDecisionEvent,
    ConceptStatus,
    QuestionConcept,
    QuestionStatus,
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
    if parent is not None and parent.subject_id != concept.subject_id:
        raise ValueError("A concept parent must belong to the same subject.")
    if parent is not None and _would_create_cycle(concept, parent):
        raise ValueError("Concept hierarchy cannot contain cycles.")


def _would_create_cycle(concept: Concept, parent: Concept) -> bool:
    current: Concept | None = parent
    while current is not None:
        if current.id == concept.id:
            return True
        current = current.parent
    return False


def _has_concept_history(db: Session, concept: Concept) -> bool:
    return (
        db.scalar(
            select(func.count(ConceptDecisionEvent.id)).where(
                ConceptDecisionEvent.subject_id == concept.subject_id,
                ConceptDecisionEvent.affected_concept_ids_json.contains(str(concept.id)),
            )
        )
        or 0
    ) > 0
