from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Concept,
    ConceptAlias,
    ConceptAssignmentRole,
    ConceptReviewItem,
    ConceptStatus,
    QuestionConcept,
    Subject,
)
from app.services import concept_service
from app.services.text_service import slugify


@dataclass(frozen=True)
class MergeConceptsInput:
    subject_name: str
    target_concept_name: str
    source_concept_names: list[str]
    dry_run: bool = True


@dataclass(frozen=True)
class MergeContext:
    subject: Subject
    target: Concept
    sources: list[Concept]
    affected_question_ids: list[int]


@dataclass
class MergeLinkState:
    target: Concept
    target_links: dict[int, QuestionConcept]
    counts: dict[str, int]


@dataclass(frozen=True)
class AliasCreateRequest:
    subject: Subject
    alias_name: str
    normalized_name: str
    target: Concept


def suggest_concept_merges(db: Session, *, subject_name: str) -> dict[str, Any]:
    subject = _require_subject(db, subject_name)
    concepts = _concept_rows(db, subject)
    one_offs = [concept for concept in concepts if concept["question_count"] <= 1]
    candidates = [
        _candidate_to_dict(concept, _suggest_target(concept["name"], concepts))
        for concept in one_offs
    ]
    _record_duplicate_review_items(db, subject, candidates)
    db.commit()
    return {
        "subject": concept_service._subject_to_dict(subject),
        "concept_count": len(concepts),
        "one_off_concept_count": len(one_offs),
        "candidates": candidates,
    }


def merge_concepts(db: Session, merge_input: MergeConceptsInput) -> dict[str, Any]:
    context = _merge_context(db, merge_input)
    report = _merge_report(merge_input, context)
    if merge_input.dry_run:
        return report | {"merged_link_count": 0, "deduped_link_count": 0}

    merged_count, deduped_count = _merge_links(db, context.target, context.sources)
    _finish_merge(db, context)
    return report | {
        "dry_run": False,
        "target_concept": concept_service._concept_to_dict(context.target),
        "merged_link_count": merged_count,
        "deduped_link_count": deduped_count,
    }


def _merge_context(db: Session, merge_input: MergeConceptsInput) -> MergeContext:
    subject = _require_subject(db, merge_input.subject_name)
    target = _require_active_concept(db, subject, merge_input.target_concept_name)
    sources = _source_concepts(
        db,
        subject,
        _normalized_source_names(merge_input),
        target.id,
    )
    return MergeContext(
        subject=subject,
        target=target,
        sources=sources,
        affected_question_ids=_affected_question_ids(db, sources),
    )


def _require_subject(db: Session, subject_name: str) -> Subject:
    subject = concept_service._get_subject_by_name(db, subject_name)
    if subject is None:
        raise ValueError(f"Unknown subject {subject_name}")
    return subject


def _require_active_concept(db: Session, subject: Subject, name: str) -> Concept:
    concept = concept_service._concept_by_normalized(db, subject, name)
    if concept is None:
        raise ValueError(f"Unknown target concept {name}")
    if concept.status != ConceptStatus.ACTIVE:
        raise ValueError(f"Concept '{concept.name}' is not active.")
    return concept


def _concept_rows(db: Session, subject: Subject) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Concept, func.count(QuestionConcept.question_id))
        .outerjoin(QuestionConcept, QuestionConcept.concept_id == Concept.id)
        .where(Concept.subject_id == subject.id, Concept.status == ConceptStatus.ACTIVE)
        .group_by(Concept.id)
        .order_by(Concept.name)
    )
    return [
        concept_service._concept_to_dict(concept) | {"question_count": question_count}
        for concept, question_count in rows
    ]


def _candidate_to_dict(
    concept: dict[str, Any],
    suggested_target: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "concept": concept,
        "suggested_target": suggested_target,
        "reason": "one-off concept" if concept["question_count"] <= 1 else "low-use concept",
    }


def _suggest_target(concept_name: str, concepts: list[dict[str, Any]]) -> dict[str, Any] | None:
    concept_tokens = _tokens(concept_name)
    candidates = [
        concept
        for concept in concepts
        if _is_merge_candidate(concept, concept_name, concept_tokens)
    ]
    return max(candidates, key=lambda concept: concept["question_count"], default=None)


def _is_merge_candidate(
    concept: dict[str, Any],
    source_name: str,
    source_tokens: set[str],
) -> bool:
    return (
        concept["name"] != source_name
        and concept["question_count"] > 0
        and bool(_tokens(concept["name"]) & source_tokens)
    )


def _tokens(name: str) -> set[str]:
    ignored = {"and", "or", "the", "a", "an", "gotcha", "gotchas"}
    return {token for token in slugify(name).split("-") if token and token not in ignored}


def _normalized_source_names(merge_input: MergeConceptsInput) -> list[str]:
    target_key = concept_service.normalize_concept_name(merge_input.target_concept_name)
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in merge_input.source_concept_names:
        _add_source_name(names, seen, raw_name, target_key)
    if not names:
        raise ValueError("Merge requires at least one source concept distinct from the target.")
    return names


def _add_source_name(
    names: list[str],
    seen: set[str],
    raw_name: str,
    target_key: str,
) -> None:
    name = raw_name.strip()
    source_key = concept_service.normalize_concept_name(name)
    if _should_add_source_name(name, source_key, target_key, seen):
        names.append(name)
        seen.add(source_key)


def _should_add_source_name(
    name: str,
    source_key: str,
    target_key: str,
    seen: set[str],
) -> bool:
    return bool(name) and source_key != target_key and source_key not in seen


def _source_concepts(
    db: Session,
    subject: Subject,
    source_names: list[str],
    target_id: int,
) -> list[Concept]:
    concepts = [_source_concept(db, subject, name) for name in source_names]
    missing = [
        name for name, concept in zip(source_names, concepts, strict=True) if concept is None
    ]
    if missing:
        raise ValueError(f"Unknown source concepts: {', '.join(missing)}")
    return [concept for concept in concepts if concept is not None and concept.id != target_id]


def _source_concept(db: Session, subject: Subject, name: str) -> Concept | None:
    return concept_service._concept_by_normalized(db, subject, name)


def _affected_question_ids(db: Session, sources: list[Concept]) -> list[int]:
    source_ids = [concept.id for concept in sources]
    return list(
        db.scalars(
            select(QuestionConcept.question_id)
            .where(QuestionConcept.concept_id.in_(source_ids))
            .distinct()
            .order_by(QuestionConcept.question_id)
        )
    )


def _merge_report(
    merge_input: MergeConceptsInput,
    context: MergeContext,
) -> dict[str, Any]:
    return {
        "dry_run": merge_input.dry_run,
        "subject": concept_service._subject_to_dict(context.subject),
        "target_concept": concept_service._concept_to_dict(context.target),
        "source_concepts": [
            concept_service._concept_to_dict(concept) for concept in context.sources
        ],
        "affected_question_count": len(context.affected_question_ids),
    }


def _merge_links(db: Session, target: Concept, sources: list[Concept]) -> tuple[int, int]:
    state = MergeLinkState(
        target=target,
        target_links=_target_links_by_question(db, target),
        counts={"merged": 0, "deduped": 0},
    )
    for source in sources:
        for link in list(source.question_links):
            _merge_link(db, state, link)
    db.flush()
    return state.counts["merged"], state.counts["deduped"]


def _merge_link(
    db: Session,
    state: MergeLinkState,
    link: QuestionConcept,
) -> None:
    target_link = state.target_links.get(link.question_id)
    if target_link is None:
        state.target_links[link.question_id] = _create_target_link(db, state.target, link)
        state.counts["merged"] += 1
    else:
        _dedupe_target_link(target_link, link)
        state.counts["deduped"] += 1
    db.delete(link)


def _create_target_link(
    db: Session,
    target: Concept,
    link: QuestionConcept,
) -> QuestionConcept:
    target_link = QuestionConcept(
        question_id=link.question_id,
        concept=target,
        role=link.role,
        rationale=link.rationale,
    )
    db.add(target_link)
    return target_link


def _dedupe_target_link(target_link: QuestionConcept, source_link: QuestionConcept) -> None:
    if source_link.role == ConceptAssignmentRole.PRIMARY:
        target_link.role = ConceptAssignmentRole.PRIMARY


def _target_links_by_question(db: Session, target: Concept) -> dict[int, QuestionConcept]:
    return {
        link.question_id: link
        for link in db.scalars(
            select(QuestionConcept).where(QuestionConcept.concept_id == target.id)
        )
    }


def _finish_merge(
    db: Session,
    context: MergeContext,
) -> None:
    for source in context.sources:
        _rehome_children(db, source=source, target=context.target)
        _archive_source_concept(source)
        _create_alias_if_available(
            db,
            subject=context.subject,
            alias_name=source.name,
            target=context.target,
        )
    concept_service._record_decision(
        db,
        concept_service.DecisionRecord(
            subject=context.subject,
            action="merge",
            concept_ids=[context.target.id, *[source.id for source in context.sources]],
            question_ids=context.affected_question_ids,
            rationale="Merged source concepts into target concept.",
            metadata={
                "target_id": context.target.id,
                "source_ids": [source.id for source in context.sources],
            },
        ),
    )
    _close_stale_reviews(db, context)
    db.commit()
    db.refresh(context.target)


def _rehome_children(db: Session, *, source: Concept, target: Concept) -> None:
    for child in source.children:
        child.parent = target
    db.flush()


def _archive_source_concept(source: Concept) -> None:
    source.status = ConceptStatus.ARCHIVED
    source.parent = None


def _create_alias_if_available(
    db: Session,
    *,
    subject: Subject,
    alias_name: str,
    target: Concept,
) -> ConceptAlias | None:
    normalized = concept_service.normalize_concept_name(alias_name, subject_name=subject.name)
    if _alias_matches_target(target, normalized) or _active_concept_shadows_alias(
        db,
        subject,
        normalized,
    ):
        return None
    existing_alias = db.scalar(
        select(ConceptAlias).where(
            ConceptAlias.subject_id == subject.id,
            ConceptAlias.alias_normalized_name == normalized,
        )
    )
    if existing_alias is not None:
        return _reactivate_alias(existing_alias, target)
    return _create_alias(
        db,
        AliasCreateRequest(
            subject=subject,
            alias_name=alias_name,
            normalized_name=normalized,
            target=target,
        ),
    )


def _alias_matches_target(target: Concept, normalized: str) -> bool:
    return target.normalized_name == normalized


def _active_concept_shadows_alias(db: Session, subject: Subject, normalized: str) -> bool:
    return (
        db.scalar(
            select(Concept).where(
                Concept.subject_id == subject.id,
                Concept.normalized_name == normalized,
                Concept.status == ConceptStatus.ACTIVE,
            )
        )
        is not None
    )


def _reactivate_alias(alias: ConceptAlias, target: Concept) -> ConceptAlias:
    alias.target_concept = target
    alias.is_active = True
    return alias


def _create_alias(
    db: Session,
    request: AliasCreateRequest,
) -> ConceptAlias:
    alias = ConceptAlias(
        subject_id=request.subject.id,
        alias_name=request.alias_name,
        alias_normalized_name=request.normalized_name,
        target_concept=request.target,
        is_active=True,
    )
    db.add(alias)
    return alias


def _record_duplicate_review_items(
    db: Session,
    subject: Subject,
    candidates: list[dict[str, Any]],
) -> None:
    for candidate in candidates:
        if candidate["suggested_target"] is not None:
            _open_duplicate_review_item(db, subject, candidate)


def _open_duplicate_review_item(
    db: Session,
    subject: Subject,
    candidate: dict[str, Any],
) -> None:
    suggested = candidate["suggested_target"]
    concept = db.get(Concept, candidate["concept"]["id"])
    concept_service._open_concept_review_item(
        db,
        concept_service.ReviewItemRequest(
            subject=subject,
            concept=concept,
            question=None,
            review_type="near_duplicate",
            reason=f"{candidate['concept']['name']} may overlap {suggested['name']}.",
            suggested_action="merge_or_dismiss",
            evidence=candidate,
        ),
    )


def _close_stale_reviews(db: Session, context: MergeContext) -> None:
    reviews = db.scalars(
        select(ConceptReviewItem).where(
            ConceptReviewItem.subject_id == context.subject.id,
            ConceptReviewItem.status == "open",
            ConceptReviewItem.review_type.in_(["near_duplicate", "unresolved_assignment"]),
            ConceptReviewItem.concept_id.in_(_merged_concept_ids(context)),
        )
    )

    for review in reviews:
        db.delete(review)


def _merged_concept_ids(context: MergeContext) -> list[int]:
    return [context.target.id, *[source.id for source in context.sources]]
