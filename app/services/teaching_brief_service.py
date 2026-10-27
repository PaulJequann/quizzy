from dataclasses import asdict, dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.learner_evidence import learner_attempt_clause
from app.models import Attempt, AttemptAnswer, Question
from app.services.grading_service import get_mastery_rows


@dataclass(frozen=True)
class TeachingBrief:
    workspace_id: int
    objectives: list[dict]
    recent_misses: list[dict]
    drafting_guidance: str


def prepare_next_practice(db: Session, workspace_id: int, subject_id: int | None = None) -> dict:
    rows = get_mastery_rows(db, subject_id=subject_id)
    rows.sort(
        key=lambda row: (
            row.mastery_state not in {"review_due", "learning"},
            row.confidence_score,
            row.concept.id,
        )
    )
    objectives = [
        {
            "concept_id": row.concept.id,
            "subject": row.concept.subject.name,
            "name": row.concept.name,
            "mastery_state": row.mastery_state,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "confidence": row.confidence_score,
            "coverage": row.coverage_score,
            "evidence": "thin" if row.primary_attempts < 3 else "observed",
            "reasons": row.reason_codes[:8],
            "recent_missed_question_ids": row.missed_question_ids[:5],
            "source_references": [source[:500] for source in row.source_references[:5]],
        }
        for row in rows[:8]
    ]
    return asdict(
        TeachingBrief(
            workspace_id,
            objectives,
            recent_misses(db, subject_id),
            "Create a short draft quiz in this workspace. Ground questions in the listed sources, "
            "address misses and due concepts, and vary retrieval. Target each listed "
            "misconception and use it as a wrong answer option. Thin evidence is uncertain. "
            "If no evidence exists, ask the workspace reviewer for a subject and source. "
            "The workspace reviewer reviews and publishes.",
        )
    )


def recent_misses(db: Session, subject_id: int | None) -> list[dict]:
    statement = (
        select(AttemptAnswer)
        .join(Question)
        .join(Attempt, Attempt.id == AttemptAnswer.attempt_id)
        .where(or_(AttemptAnswer.is_correct.is_(False), AttemptAnswer.misconception.is_not(None)))
        .where(learner_attempt_clause())
    )
    if subject_id is not None:
        statement = statement.where(Question.subject_id == subject_id)
    answers = db.scalars(
        statement.order_by(AttemptAnswer.answered_at.desc(), AttemptAnswer.id.desc()).limit(8)
    )
    return [
        {
            "question_id": answer.question_id,
            "question": (answer.question_stem_snapshot or answer.question.stem)[:1500],
            "question_text_source": "attempt_snapshot"
            if answer.question_stem_snapshot
            else "current_question",
            "selected_answer": answer.selected_choice_text_snapshot[:1000],
            "correct_answer": answer.correct_choice_text_snapshot[:1000],
            "misconception": answer.misconception,
            "attempt_id": answer.attempt_id,
            "actor_id": answer.attempt.actor_id,
            "actor_name": answer.attempt.actor_name,
            "actor_role": answer.attempt.actor_role,
            "answered_at": answer.answered_at.isoformat() if answer.answered_at else None,
            "source_document_id": answer.question.source_document_id,
            "source_snapshot_id": answer.question.source_snapshot_id,
        }
        for answer in answers
    ]
