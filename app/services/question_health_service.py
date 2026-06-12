from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Attempt,
    AttemptAnswer,
    Question,
    QuestionHealthState,
    QuestionReviewItem,
    QuestionStatus,
)

REVIEW_STATUS_OPEN = "open"


@dataclass(frozen=True)
class QuestionHealthSummary:
    question_id: int
    state: QuestionHealthState
    score: float | None
    attempt_count: int
    correct_count: int
    accuracy: float | None
    reason: str


@dataclass(frozen=True)
class ReviewEvidence:
    reason: str
    suggested_action: str
    attempt_count: int
    correct_count: int


def initialize_for_batch(db: Session, generation_batch_id: int) -> None:
    questions = db.scalars(
        select(Question).where(Question.generation_batch_id == generation_batch_id)
    ).all()
    for question in questions:
        question.health_state = QuestionHealthState.UNTESTED
        question.health_score = None
        question.last_health_checked_at = datetime.now(UTC)
    db.commit()


def record_attempt_evidence(db: Session, attempt_id: int) -> list[QuestionHealthSummary]:
    attempt = db.scalar(
        select(Attempt).where(Attempt.id == attempt_id).options(selectinload(Attempt.answers))
    )
    if attempt is None:
        return []

    summaries = [score_question_health(db, answer.question_id) for answer in attempt.answers]
    db.commit()
    return summaries


def score_question_health(db: Session, question_id: int) -> QuestionHealthSummary:
    question = db.scalar(
        select(Question)
        .where(Question.id == question_id)
        .options(selectinload(Question.choices), selectinload(Question.review_items))
    )
    if question is None:
        raise ValueError(f"Unknown question id {question_id}")

    attempt_count, correct_count = _attempt_counts(db, question.id)
    accuracy = _accuracy(attempt_count, correct_count)
    state, score, reason, action = _classify_question(question, attempt_count, accuracy)

    question.health_state = state
    question.health_score = score
    question.last_health_checked_at = datetime.now(UTC)
    if action:
        _ensure_open_review_item(
            db,
            question,
            ReviewEvidence(
                reason=reason,
                suggested_action=action,
                attempt_count=attempt_count,
                correct_count=correct_count,
            ),
        )

    return QuestionHealthSummary(
        question_id=question.id,
        state=state,
        score=score,
        attempt_count=attempt_count,
        correct_count=correct_count,
        accuracy=accuracy,
        reason=reason,
    )


def audit_subject_question_health(db: Session, subject_id: int) -> list[QuestionHealthSummary]:
    question_ids = db.scalars(
        select(Question.id).where(
            Question.subject_id == subject_id,
            Question.status == QuestionStatus.ACTIVE,
        )
    ).all()
    summaries = [score_question_health(db, question_id) for question_id in question_ids]
    db.commit()
    return summaries


def list_question_review_queue(
    db: Session,
    *,
    subject_id: int | None = None,
    status: str = REVIEW_STATUS_OPEN,
) -> list[dict[str, Any]]:
    statement = (
        select(QuestionReviewItem)
        .where(QuestionReviewItem.status == status)
        .options(
            selectinload(QuestionReviewItem.subject),
            selectinload(QuestionReviewItem.question).selectinload(Question.choices),
            selectinload(QuestionReviewItem.question).selectinload(Question.concepts),
        )
        .order_by(QuestionReviewItem.created_at.desc(), QuestionReviewItem.id.desc())
    )
    if subject_id is not None:
        statement = statement.where(QuestionReviewItem.subject_id == subject_id)

    return [_review_item_to_dict(item) for item in db.scalars(statement)]


def prepare_question_repair_context(db: Session, question_id: int) -> dict[str, Any]:
    question = db.scalar(
        select(Question)
        .where(Question.id == question_id)
        .options(
            selectinload(Question.subject),
            selectinload(Question.choices),
            selectinload(Question.concepts),
            selectinload(Question.source_document),
            selectinload(Question.review_items),
        )
    )
    if question is None:
        raise ValueError(f"Unknown question id {question_id}")

    summary = score_question_health(db, question.id)
    db.commit()
    return {
        "question": _question_to_dict(question),
        "health": _summary_to_dict(summary),
        "choice_distribution": _choice_distribution(db, question),
        "open_review_items": [
            _review_item_to_dict(item)
            for item in question.review_items
            if item.status == REVIEW_STATUS_OPEN
        ],
        "source_document": _source_document_context(question),
    }


def resolve_question_review_item(
    db: Session,
    item_id: int,
    *,
    action: str,
    notes: str = "",
) -> dict[str, Any]:
    item = db.get(QuestionReviewItem, item_id)
    if item is None:
        raise ValueError(f"Unknown question review item id {item_id}")

    item.status = "dismissed" if action == "dismiss" else "resolved"
    item.resolution_notes = notes.strip() or action
    db.commit()
    db.refresh(item)
    return _review_item_to_dict(item)


def _attempt_counts(db: Session, question_id: int) -> tuple[int, int]:
    row = db.execute(
        select(
            func.count(AttemptAnswer.id),
            func.coalesce(func.sum(AttemptAnswer.is_correct.cast(Integer)), 0),
        ).where(AttemptAnswer.question_id == question_id)
    ).one()
    return int(row[0]), int(row[1])


def _accuracy(attempt_count: int, correct_count: int) -> float | None:
    if attempt_count == 0:
        return None
    return round((correct_count / attempt_count) * 100, 2)


def _classify_question(
    question: Question,
    attempt_count: int,
    accuracy: float | None,
) -> tuple[QuestionHealthState, float | None, str, str | None]:
    if question.status == QuestionStatus.ARCHIVED:
        return QuestionHealthState.ARCHIVED, 0, "Question is archived.", None
    if attempt_count == 0 or accuracy is None:
        return QuestionHealthState.UNTESTED, None, "Question has not been attempted yet.", None
    flagged = _flagged_health_state(attempt_count, accuracy)
    if flagged:
        return flagged
    return QuestionHealthState.HEALTHY, accuracy, "Question attempt evidence is acceptable.", None


def _flagged_health_state(
    attempt_count: int,
    accuracy: float,
) -> tuple[QuestionHealthState, float, str, str] | None:
    if attempt_count >= 3 and accuracy <= 25:
        return (
            QuestionHealthState.SUSPECT,
            accuracy,
            "Repeated misses suggest ambiguity.",
            "revise_stem",
        )
    if accuracy == 0:
        return (
            QuestionHealthState.NEEDS_REVIEW,
            accuracy,
            "The first observed attempt missed this question.",
            "clarify_explanation",
        )
    if attempt_count >= 5 and accuracy >= 95:
        return (
            QuestionHealthState.NEEDS_REVIEW,
            accuracy,
            "Question may be too easy.",
            "improve_distractors",
        )
    return None


def _ensure_open_review_item(
    db: Session,
    question: Question,
    evidence: ReviewEvidence,
) -> None:
    existing = next(
        (
            item
            for item in question.review_items
            if item.status == REVIEW_STATUS_OPEN
            and item.suggested_action == evidence.suggested_action
        ),
        None,
    )
    evidence_json = _review_evidence_json(evidence)
    if existing:
        existing.reason = evidence.reason
        existing.evidence_json = evidence_json
        return

    db.add(
        QuestionReviewItem(
            question=question,
            subject=question.subject,
            generation_batch=question.generation_batch,
            reason=evidence.reason,
            suggested_action=evidence.suggested_action,
            evidence_json=evidence_json,
        )
    )


def _review_evidence_json(evidence: ReviewEvidence) -> str:
    return json.dumps(
        {
            "attempt_count": evidence.attempt_count,
            "correct_count": evidence.correct_count,
            "accuracy": _accuracy(evidence.attempt_count, evidence.correct_count),
        },
        sort_keys=True,
    )


def _choice_distribution(db: Session, question: Question) -> list[dict[str, Any]]:
    selected_counts = {
        choice_id: int(count)
        for choice_id, count in (
            db.execute(
                select(AttemptAnswer.selected_choice_id, func.count(AttemptAnswer.id))
                .where(AttemptAnswer.question_id == question.id)
                .group_by(AttemptAnswer.selected_choice_id)
            ).all()
        )
        if choice_id is not None
    }
    return [
        {
            "choice_id": choice.id,
            "text": choice.text,
            "is_correct": choice.is_correct,
            "selected_count": int(selected_counts.get(choice.id, 0)),
        }
        for choice in question.choices
    ]


def _review_item_to_dict(item: QuestionReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "question_id": item.question_id,
        "subject_id": item.subject_id,
        "subject_name": item.subject.name,
        "generation_batch_id": item.generation_batch_id,
        "reason": item.reason,
        "suggested_action": item.suggested_action,
        "evidence": json.loads(item.evidence_json or "{}"),
        "status": item.status,
        "resolution_notes": item.resolution_notes,
        "question_stem": item.question.stem,
        "concepts": [concept.name for concept in item.question.concepts],
        "created_at": item.created_at.isoformat(),
    }


def _question_to_dict(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "subject": question.subject.name,
        "stem": question.stem,
        "choices": [
            {"id": choice.id, "text": choice.text, "is_correct": choice.is_correct}
            for choice in question.choices
        ],
        "explanation": question.explanation,
        "difficulty": question.difficulty.value,
        "status": question.status.value,
        "health_state": question.health_state.value,
        "source_reference": question.source_reference,
        "source_excerpt": question.source_excerpt,
        "concepts": [concept.name for concept in question.concepts],
    }


def _summary_to_dict(summary: QuestionHealthSummary) -> dict[str, Any]:
    return {
        "question_id": summary.question_id,
        "state": summary.state.value,
        "score": summary.score,
        "attempt_count": summary.attempt_count,
        "correct_count": summary.correct_count,
        "accuracy": summary.accuracy,
        "reason": summary.reason,
    }


def _source_document_context(question: Question) -> dict[str, Any] | None:
    document = question.source_document
    if document is None:
        return None
    return {
        "id": document.id,
        "title": document.title,
        "path": document.path,
        "content": document.content,
    }
