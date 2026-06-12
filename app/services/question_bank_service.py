from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Choice,
    Question,
    QuestionConcept,
    QuestionGenerationBatch,
    QuestionStatus,
    SourceDocument,
    Subject,
)
from app.services import question_health_service
from app.services.quiz_service import get_or_create_concepts, get_or_create_subject, slugify
from app.services.settings_service import get_default_difficulty

MAX_CONCEPTS_PER_GENERATED_QUESTION = 2


@dataclass(frozen=True)
class BatchMetadata:
    generator_type: str = "mcp_agent"
    generator_name: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class QuestionBankCreateInput:
    subject_name: str
    questions: list[Any]
    source_document_id: int | None = None
    batch_metadata: BatchMetadata | None = None


@dataclass(frozen=True)
class QuestionBuildInput:
    subject: Subject
    question_input: Any
    batch: QuestionGenerationBatch
    fallback_source_document_id: int | None


def validate_question_batch(
    db: Session,
    questions: list[Any],
    *,
    source_document_id: int | None = None,
) -> None:
    for question_input in questions:
        _validate_question(question_input, fallback_source_document_id=source_document_id)
        _source_reference(db, question_input, fallback_source_document_id=source_document_id)


def create_generated_questions(
    db: Session,
    data: QuestionBankCreateInput,
) -> dict[str, Any]:
    validate_question_batch(db, data.questions, source_document_id=data.source_document_id)
    subject = get_or_create_subject(db, data.subject_name)
    metadata = data.batch_metadata or BatchMetadata()
    batch = QuestionGenerationBatch(
        subject=subject,
        source_document_id=data.source_document_id,
        generator_type=metadata.generator_type,
        generator_name=metadata.generator_name,
        settings_json=json.dumps(metadata.settings, sort_keys=True),
        notes=metadata.notes,
    )
    db.add(batch)
    db.flush()

    inserted = [
        _build_question(
            db,
            QuestionBuildInput(
                subject=subject,
                question_input=question_input,
                batch=batch,
                fallback_source_document_id=data.source_document_id,
            ),
        )
        for question_input in data.questions
    ]
    db.commit()
    question_health_service.initialize_for_batch(db, batch.id)
    return _batch_result(batch, inserted)


def list_question_bank(
    db: Session,
    *,
    subject_id: int | None = None,
    status: QuestionStatus | None = None,
) -> list[dict[str, Any]]:
    statement = (
        select(Question)
        .options(
            selectinload(Question.subject),
            selectinload(Question.choices),
            selectinload(Question.concepts),
            selectinload(Question.source_document),
        )
        .order_by(Question.created_at.desc(), Question.id.desc())
    )
    if subject_id is not None:
        statement = statement.where(Question.subject_id == subject_id)
    if status is not None:
        statement = statement.where(Question.status == status)
    return [_question_to_dict(question) for question in db.scalars(statement)]


def question_ids_for_batch(db: Session, generation_batch_id: int) -> list[int]:
    return list(
        db.scalars(
            select(Question.id)
            .where(Question.generation_batch_id == generation_batch_id)
            .order_by(Question.id)
        )
    )


def _build_question(
    db: Session,
    data: QuestionBuildInput,
) -> Question:
    question_input = data.question_input
    concepts = get_or_create_concepts(
        db,
        _normalized_concept_names(question_input),
        data.subject,
    )
    question = Question(
        subject=data.subject,
        stem=question_input.stem.strip(),
        explanation=(question_input.explanation or "").strip() or None,
        difficulty=question_input.difficulty or get_default_difficulty(db),
        status=QuestionStatus.ACTIVE,
        source_reference=_source_reference(
            db,
            question_input,
            fallback_source_document_id=data.fallback_source_document_id,
        ),
        source_document_id=_question_source_document_id(
            question_input,
            data.fallback_source_document_id,
        ),
        source_excerpt=(question_input.source_excerpt or "").strip() or None,
        generation_batch=data.batch,
    )
    db.add(question)
    question.choices = [
        Choice(
            text=choice_input.text.strip(),
            is_correct=choice_input.is_correct,
            order_index=index,
        )
        for index, choice_input in enumerate(question_input.choices, start=1)
    ]
    question.concept_links = [
        QuestionConcept(concept=concept, is_primary=index == 0)
        for index, concept in enumerate(concepts)
    ]
    return question


def _validate_question(
    question_input: Any,
    *,
    fallback_source_document_id: int | None,
) -> None:
    _validate_choices(question_input)
    _validate_source(question_input, fallback_source_document_id=fallback_source_document_id)
    _validate_concepts(question_input)


def _validate_choices(question_input: Any) -> None:
    if len(question_input.choices) != 4:
        raise ValueError("Generated questions must have exactly four choices.")
    if sum(choice.is_correct for choice in question_input.choices) != 1:
        raise ValueError("Generated questions must have exactly one correct choice.")


def _validate_source(question_input: Any, *, fallback_source_document_id: int | None) -> None:
    if not _has_source(question_input, fallback_source_document_id=fallback_source_document_id):
        raise ValueError("Generated questions require source_document_id or source_reference.")


def _validate_concepts(question_input: Any) -> None:
    concept_names = _normalized_concept_names(question_input)
    if not concept_names:
        raise ValueError("Generated questions require at least one concept.")
    if len(concept_names) > MAX_CONCEPTS_PER_GENERATED_QUESTION:
        raise ValueError("Generated questions can tag at most two concepts.")


def _has_source(question_input: Any, *, fallback_source_document_id: int | None) -> bool:
    return (
        question_input.source_document_id is not None
        or fallback_source_document_id is not None
        or bool((question_input.source_reference or "").strip())
    )


def _question_source_document_id(
    question_input: Any,
    fallback_source_document_id: int | None,
) -> int | None:
    return question_input.source_document_id or fallback_source_document_id


def _normalized_concept_names(question_input: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in question_input.concept_names:
        name = raw_name.strip()
        slug = slugify(name)
        if name and slug not in seen:
            names.append(name)
            seen.add(slug)
    return names


def _source_reference(
    db: Session,
    question_input: Any,
    *,
    fallback_source_document_id: int | None,
) -> str:
    explicit_reference = (question_input.source_reference or "").strip()
    if explicit_reference:
        return explicit_reference
    source_document_id = _question_source_document_id(question_input, fallback_source_document_id)
    if source_document_id is None:
        raise ValueError("Generated questions require source_document_id or source_reference.")
    document = db.scalar(select(SourceDocument).where(SourceDocument.id == source_document_id))
    if document is None:
        raise ValueError(f"Unknown source document id {source_document_id}")
    return document.path


def _batch_result(batch: QuestionGenerationBatch, questions: list[Question]) -> dict[str, Any]:
    return {
        "generation_batch": {
            "id": batch.id,
            "subject_id": batch.subject_id,
            "source_document_id": batch.source_document_id,
            "generator_type": batch.generator_type,
            "generator_name": batch.generator_name,
            "settings": json.loads(batch.settings_json or "{}"),
            "notes": batch.notes,
        },
        "questions_created": len(questions),
        "question_ids": [question.id for question in questions],
    }


def _question_to_dict(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "subject_id": question.subject_id,
        "subject": question.subject.name,
        "stem": question.stem,
        "difficulty": question.difficulty.value,
        "status": question.status.value,
        "health_state": question.health_state.value,
        "health_score": question.health_score,
        "source_document_id": question.source_document_id,
        "source_reference": question.source_reference,
        "source_excerpt": question.source_excerpt,
        "generation_batch_id": question.generation_batch_id,
        "concepts": [concept.name for concept in question.concepts],
        "choices": [
            {"id": choice.id, "text": choice.text, "is_correct": choice.is_correct}
            for choice in question.choices
        ],
    }
