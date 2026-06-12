from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Concept, QuestionConcept, Subject
from app.services.quiz_service import get_or_create_concepts, slugify


@dataclass(frozen=True)
class MergeConceptsInput:
    subject_name: str
    target_concept_name: str
    source_concept_names: list[str]
    dry_run: bool = True


def suggest_concept_merges(db: Session, *, subject_name: str) -> dict[str, Any]:
    subject = _get_subject(db, subject_name)
    concepts = _concept_rows(db, subject)
    one_offs = [concept for concept in concepts if concept["question_count"] <= 1]
    candidates = [
        _candidate_to_dict(concept, _suggest_target(concept["name"], concepts))
        for concept in one_offs
    ]
    return {
        "subject": _subject_to_dict(subject),
        "concept_count": len(concepts),
        "one_off_concept_count": len(one_offs),
        "candidates": candidates,
    }


def merge_concepts(db: Session, merge_input: MergeConceptsInput) -> dict[str, Any]:
    subject = _get_subject(db, merge_input.subject_name)
    source_names = _normalized_source_names(merge_input)
    target = _target_concept(
        db, subject, merge_input.target_concept_name, create=not merge_input.dry_run
    )
    target_id = target.id if target is not None else None
    sources = _source_concepts(db, subject, source_names, target_id)
    affected_questions = _affected_question_count(db, sources)
    report = {
        "dry_run": merge_input.dry_run,
        "subject": _subject_to_dict(subject),
        "target_concept": _target_to_dict(target, merge_input.target_concept_name, subject),
        "source_concepts": [_concept_to_dict(concept) for concept in sources],
        "affected_question_count": affected_questions,
    }
    if merge_input.dry_run:
        return report | {"merged_link_count": 0, "deduped_link_count": 0}

    if target is None:
        raise ValueError("Target concept could not be created.")
    merged_count, deduped_count = _merge_links(db, target, sources)
    db.execute(delete(Concept).where(Concept.id.in_([source.id for source in sources])))
    db.commit()
    db.refresh(target)
    return report | {
        "dry_run": False,
        "target_concept": _concept_to_dict(target),
        "merged_link_count": merged_count,
        "deduped_link_count": deduped_count,
    }


def _get_subject(db: Session, subject_name: str) -> Subject:
    subject = db.scalar(select(Subject).where(Subject.slug == slugify(subject_name)))
    if subject is None:
        raise ValueError(f"Unknown subject {subject_name}")
    return subject


def _concept_rows(db: Session, subject: Subject) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Concept, func.count(QuestionConcept.question_id))
        .outerjoin(QuestionConcept, QuestionConcept.concept_id == Concept.id)
        .where(Concept.subject_id == subject.id)
        .group_by(Concept.id)
        .order_by(Concept.name)
    )
    return [
        _concept_to_dict(concept) | {"question_count": question_count}
        for concept, question_count in rows
    ]


def _candidate_to_dict(
    concept: dict[str, Any], suggested_target: dict[str, Any] | None
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
        if concept["name"] != concept_name
        and concept["question_count"] > 0
        and _tokens(concept["name"]) & concept_tokens
    ]
    return max(candidates, key=lambda concept: concept["question_count"], default=None)


def _tokens(name: str) -> set[str]:
    ignored = {"and", "or", "the", "a", "an", "gotcha", "gotchas"}
    return {token for token in slugify(name).split("-") if token and token not in ignored}


def _normalized_source_names(merge_input: MergeConceptsInput) -> list[str]:
    target_slug = slugify(merge_input.target_concept_name)
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in merge_input.source_concept_names:
        name = raw_name.strip()
        source_slug = slugify(name)
        if not _should_merge_source(name, source_slug, target_slug, seen):
            continue
        names.append(name)
        seen.add(source_slug)
    if not names:
        raise ValueError("Merge requires at least one source concept distinct from the target.")
    return names


def _should_merge_source(name: str, source_slug: str, target_slug: str, seen: set[str]) -> bool:
    if not name:
        return False
    if source_slug == target_slug:
        return False
    return source_slug not in seen


def _target_concept(db: Session, subject: Subject, name: str, *, create: bool) -> Concept | None:
    target = db.scalar(
        select(Concept).where(Concept.subject_id == subject.id, Concept.slug == slugify(name))
    )
    if target is not None or not create:
        return target
    [created_target] = get_or_create_concepts(db, [name], subject)
    return created_target


def _source_concepts(
    db: Session,
    subject: Subject,
    source_names: list[str],
    target_id: int | None,
) -> list[Concept]:
    source_slugs = [slugify(name) for name in source_names]
    concepts_by_slug = {
        concept.slug: concept
        for concept in db.scalars(
            select(Concept)
            .where(Concept.subject_id == subject.id, Concept.slug.in_(source_slugs))
            .options(selectinload(Concept.question_links))
        )
    }
    missing = [name for name in source_names if slugify(name) not in concepts_by_slug]
    if missing:
        raise ValueError(f"Unknown source concepts: {', '.join(missing)}")
    return [concept for slug in source_slugs if (concept := concepts_by_slug[slug]).id != target_id]


def _affected_question_count(db: Session, sources: list[Concept]) -> int:
    source_ids = [concept.id for concept in sources]
    return (
        db.scalar(
            select(func.count(func.distinct(QuestionConcept.question_id))).where(
                QuestionConcept.concept_id.in_(source_ids)
            )
        )
        or 0
    )


def _merge_links(
    db: Session,
    target: Concept,
    sources: list[Concept],
) -> tuple[int, int]:
    merged_count = 0
    deduped_count = 0
    target_links = _target_links_by_question(db, target)
    for source in sources:
        for link in list(source.question_links):
            target_link = target_links.get(link.question_id)
            if target_link is None:
                target_link = QuestionConcept(
                    question_id=link.question_id,
                    concept=target,
                    is_primary=link.is_primary,
                )
                db.add(target_link)
                target_links[link.question_id] = target_link
                merged_count += 1
            else:
                target_link.is_primary = target_link.is_primary or link.is_primary
                deduped_count += 1
            db.delete(link)
    db.flush()
    return merged_count, deduped_count


def _target_links_by_question(db: Session, target: Concept) -> dict[int, QuestionConcept]:
    return {
        link.question_id: link
        for link in db.scalars(
            select(QuestionConcept).where(QuestionConcept.concept_id == target.id)
        )
    }


def _subject_to_dict(subject: Subject) -> dict[str, Any]:
    return {
        "id": subject.id,
        "name": subject.name,
        "slug": subject.slug,
        "description": subject.description,
    }


def _concept_to_dict(concept: Concept) -> dict[str, Any]:
    return {
        "id": concept.id,
        "name": concept.name,
        "slug": concept.slug,
        "subject_id": concept.subject_id,
    }


def _target_to_dict(concept: Concept | None, target_name: str, subject: Subject) -> dict[str, Any]:
    if concept is not None:
        return _concept_to_dict(concept)
    return {
        "id": None,
        "name": target_name.strip(),
        "slug": slugify(target_name),
        "subject_id": subject.id,
    }
