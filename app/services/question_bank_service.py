from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ConceptAssignmentRole,
    Question,
    QuestionConcept,
    QuestionGenerationBatch,
    QuestionStatus,
    SourceDocument,
    SourceDocumentSnapshot,
    Subject,
)
from app.services import concept_proposal_service, concept_service, question_health_service
from app.services.question_content import apply_content, is_open_ended, rubric_to_dict
from app.services.quiz_service import get_or_create_subject
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
    approve_new_concepts: bool = False
    save_unresolved_as_draft: bool = False
    draft_review_type: str = "unresolved_assignment"
    draft_review_reason: str = ""
    draft_suggested_action: str = "approve_or_reject_concept"
    draft_review_evidence: dict[str, Any] | None = None
    exam_only: bool = False


@dataclass(frozen=True)
class QuestionBuildInput:
    subject: Subject
    question_input: Any
    batch: QuestionGenerationBatch
    fallback_source_document_id: int | None
    approve_new_concepts: bool
    save_unresolved_as_draft: bool
    draft_review_type: str
    draft_review_reason: str
    draft_suggested_action: str
    draft_review_evidence: dict[str, Any] | None
    exam_only: bool


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
                approve_new_concepts=data.approve_new_concepts,
                save_unresolved_as_draft=data.save_unresolved_as_draft,
                draft_review_type=data.draft_review_type,
                draft_review_reason=data.draft_review_reason,
                draft_suggested_action=data.draft_suggested_action,
                draft_review_evidence=data.draft_review_evidence,
                exam_only=data.exam_only,
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
            selectinload(Question.key_points),
            selectinload(Question.concept_links).selectinload(QuestionConcept.concept),
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
    question = _question_from_build_input(db, data)
    question.exam_only = data.exam_only
    apply_content(question, data.question_input)
    db.add(question)
    db.flush()
    assignments = _resolve_build_assignments(db, data, question)
    concept_service.apply_question_assignments(question, assignments)
    _record_build_assignments(db, data, question, assignments)
    return question


def _question_from_build_input(db: Session, data: QuestionBuildInput) -> Question:
    question_input = data.question_input
    return Question(
        subject=data.subject,
        stem=question_input.stem.strip(),
        explanation=(question_input.explanation or "").strip() or None,
        difficulty=question_input.difficulty or get_default_difficulty(db),
        status=_question_status(data),
        source_reference=_source_reference(
            db,
            question_input,
            fallback_source_document_id=data.fallback_source_document_id,
        ),
        source_document_id=_question_source_document_id(
            question_input,
            data.fallback_source_document_id,
        ),
        source_snapshot_id=_question_source_snapshot_id(
            db,
            question_input,
            fallback_source_document_id=data.fallback_source_document_id,
        ),
        source_excerpt=(question_input.source_excerpt or "").strip() or None,
        generation_batch=data.batch,
    )


def _question_status(data: QuestionBuildInput) -> QuestionStatus:
    return QuestionStatus.DRAFT if data.save_unresolved_as_draft else QuestionStatus.ACTIVE


def _resolve_build_assignments(
    db: Session,
    data: QuestionBuildInput,
    question: Question,
) -> list[concept_service.ResolvedConceptAssignment]:
    proposals = _concept_proposals(data.question_input)
    if data.save_unresolved_as_draft:
        return concept_proposal_service.create_proposed_assignments(
            db,
            concept_service.ProposedAssignmentRequest(
                subject=data.subject,
                proposals=proposals,
                question=question,
                actor="mcp_agent",
                source="mcp",
                review_type=data.draft_review_type,
                review_reason=data.draft_review_reason,
                suggested_action=data.draft_suggested_action,
                review_evidence=data.draft_review_evidence,
            ),
        )
    return concept_service.resolve_concept_assignments(
        db,
        concept_service.AssignmentResolutionRequest(
            subject=data.subject,
            proposals=proposals,
            allow_new_active=data.approve_new_concepts,
            actor="mcp_agent",
            source="mcp",
        ),
    )


def _record_build_assignments(
    db: Session,
    data: QuestionBuildInput,
    question: Question,
    assignments: list[concept_service.ResolvedConceptAssignment],
) -> None:
    concept_service.record_question_assignment_decision(
        db,
        concept_service.QuestionAssignmentDecision(
            subject=data.subject,
            question=question,
            assignments=assignments,
            actor="mcp_agent",
            source="mcp",
        ),
    )


def _validate_question(
    question_input: Any,
    *,
    fallback_source_document_id: int | None,
) -> None:
    _validate_choices(question_input)
    _validate_source(question_input, fallback_source_document_id=fallback_source_document_id)
    _validate_concepts(question_input)


def _validate_choices(question_input: Any) -> None:
    if is_open_ended(question_input):
        return
    if len(question_input.choices) != 4:
        raise ValueError("Generated questions must have exactly four choices.")
    if sum(choice.is_correct for choice in question_input.choices) != 1:
        raise ValueError("Generated questions must have exactly one correct choice.")


def _validate_source(question_input: Any, *, fallback_source_document_id: int | None) -> None:
    if not _has_source(question_input, fallback_source_document_id=fallback_source_document_id):
        raise ValueError("Generated questions require source_document_id or source_reference.")


def _validate_concepts(question_input: Any) -> None:
    proposals = _concept_proposals(question_input)
    if not proposals:
        raise ValueError("Generated questions require at least one concept.")
    if len(proposals) > MAX_CONCEPTS_PER_GENERATED_QUESTION:
        raise ValueError("Generated questions can tag at most two concepts.")
    primary_count = sum(proposal.role == ConceptAssignmentRole.PRIMARY for proposal in proposals)
    if primary_count != 1:
        raise ValueError("Generated questions require exactly one primary concept.")


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


def _question_source_snapshot_id(
    db: Session,
    question_input: Any,
    *,
    fallback_source_document_id: int | None,
) -> int | None:
    explicit_snapshot_id = getattr(question_input, "source_snapshot_id", None)
    source_document_id = _question_source_document_id(question_input, fallback_source_document_id)
    if explicit_snapshot_id is not None:
        _validate_source_snapshot(
            db,
            snapshot_id=explicit_snapshot_id,
            source_document_id=source_document_id,
        )
        return explicit_snapshot_id
    if source_document_id is None:
        return None
    document = db.get(SourceDocument, source_document_id)
    if document is None:
        raise ValueError(f"Unknown source document id {source_document_id}")
    return document.current_snapshot_id


def _validate_source_snapshot(
    db: Session,
    *,
    snapshot_id: int,
    source_document_id: int | None,
) -> None:
    if source_document_id is None:
        raise ValueError("source_snapshot_id requires source_document_id")
    snapshot = db.get(SourceDocumentSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"Unknown source snapshot id {snapshot_id}")
    if snapshot.source_document_id != source_document_id:
        raise ValueError("source_snapshot_id must belong to source_document_id")


def _concept_proposals(question_input: Any) -> list[concept_service.ConceptProposal]:
    proposals: list[concept_service.ConceptProposal] = []
    seen: set[str] = set()
    for raw_proposal in question_input.concept_proposals:
        proposal = _proposal_to_domain(raw_proposal)
        key = concept_service.normalize_concept_name(proposal.name)
        if proposal.name.strip() and key not in seen:
            proposals.append(proposal)
            seen.add(key)
    return proposals


def _proposal_to_domain(raw_proposal: Any) -> concept_service.ConceptProposal:
    role = raw_proposal.role
    if isinstance(role, str):
        role = ConceptAssignmentRole(role)
    return concept_service.ConceptProposal(
        name=raw_proposal.name,
        parent_name=raw_proposal.parent_name,
        role=role,
        rationale=raw_proposal.rationale,
    )


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
    document = db.get(SourceDocument, source_document_id)
    if document is None:
        raise ValueError(f"Unknown source document id {source_document_id}")
    return document.source_key


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
        "question_type": question.question_type.value,
        "exam_only": question.exam_only,
        "stem": question.stem,
        **rubric_to_dict(question),
        "difficulty": question.difficulty.value,
        "status": question.status.value,
        "health_state": question.health_state.value,
        "health_score": question.health_score,
        "source_document_id": question.source_document_id,
        "source_snapshot_id": question.source_snapshot_id,
        "source_reference": question.source_reference,
        "source_excerpt": question.source_excerpt,
        "generation_batch_id": question.generation_batch_id,
        "concepts": [
            {
                "id": link.concept.id,
                "name": link.concept.name,
                "role": link.role.value,
                "status": link.concept.status.value,
            }
            for link in sorted(
                question.concept_links,
                key=lambda link: (link.role.value, link.concept.name.casefold()),
            )
        ],
        "choices": [
            {"id": choice.id, "text": choice.text, "is_correct": choice.is_correct}
            for choice in question.choices
        ],
    }
