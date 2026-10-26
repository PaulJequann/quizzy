from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.learner_evidence import is_learner_attempt, learner_attempt_clause
from app.models import (
    Attempt,
    AttemptAnswer,
    AttemptRole,
    Concept,
    ConceptAssignmentRole,
    ConceptReviewState,
    ConceptStatus,
    Question,
    QuestionConcept,
    QuestionHealthState,
    QuestionStatus,
)
from app.workspace_database import WorkspaceSession

SHAKY = 1
EFFORTFUL = 2
FLUENT = 3
VALID_FRICTION_RATINGS = {SHAKY, EFFORTFUL, FLUENT}

INTERVAL_BANDS = [0, 1, 3, 7, 14, 30, 60, 120]
MIN_EASE_FACTOR = 1.3
MAX_EASE_FACTOR = 2.8
DEFAULT_EASE_FACTOR = 2.0
MIN_SPACING_HOURS = 12
MIN_FAMILY_REPEAT_HOURS = 12

SCHEDULE_BLOCKING_HEALTH_STATES = {
    QuestionHealthState.SUSPECT,
    QuestionHealthState.ARCHIVED,
}


@dataclass(frozen=True)
class ConceptScheduleUpdate:
    concept_id: int
    previous_due_at: datetime | None
    due_at: datetime | None
    previous_interval_days: int
    interval_days: int
    spacing_eligible: bool
    reason_codes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScheduleContext:
    answer: AttemptAnswer
    concept: Concept
    role: ConceptAssignmentRole
    friction_rating: int


@dataclass(frozen=True)
class ScheduleEvent:
    is_correct: bool
    friction_rating: int
    spacing_eligible: bool


def validate_retrieval_feedback(answer: AttemptAnswer, friction_rating: int) -> None:
    if friction_rating not in VALID_FRICTION_RATINGS:
        raise ValueError("Retrieval friction rating must be 1, 2, or 3.")
    if friction_rating == FLUENT and not answer.is_correct:
        raise ValueError("Fluent retrieval feedback requires a correct answer.")


def record_retrieval_feedback(
    db: Session,
    *,
    attempt_answer_id: int,
    friction_rating: int,
    response_time_ms: int | None = None,
) -> list[ConceptScheduleUpdate]:
    answer = _attempt_answer(db, attempt_answer_id)
    if isinstance(db, WorkspaceSession) and db.access.attempt_role != AttemptRole.LEARNER:
        raise ValueError("Retrieval feedback requires a learner session.")
    if not is_learner_attempt(answer.attempt):
        raise ValueError("Retrieval feedback requires a learner attempt.")
    validate_retrieval_feedback(answer, friction_rating)
    if response_time_ms is not None and response_time_ms < 0:
        raise ValueError("Response time cannot be negative.")

    answer.retrieval_friction_rating = friction_rating
    answer.response_time_ms = response_time_ms
    now = datetime.now(UTC)
    updates = [
        update_concept_schedule_from_answer(
            db,
            context=ScheduleContext(
                answer=answer,
                concept=link.concept,
                role=link.role,
                friction_rating=friction_rating,
            ),
            now=now,
        )
        for link in answer.question.concept_links
        if link.concept.status == ConceptStatus.ACTIVE
    ]
    db.commit()
    return updates


def update_concept_schedule_from_answer(
    db: Session,
    *,
    context: ScheduleContext,
    now: datetime,
) -> ConceptScheduleUpdate:
    if isinstance(db, WorkspaceSession) and db.access.attempt_role != AttemptRole.LEARNER:
        raise ValueError("Only learner sessions can change review schedules.")
    if not is_learner_attempt(context.answer.attempt):
        raise ValueError("Only learner attempts can change review schedules.")
    state = _review_state_for_concept(db, context.concept)
    previous_due_at = state.due_at
    previous_interval_days = state.interval_days
    eligible, reason_codes = spacing_eligible(
        db,
        context=context,
        state=state,
        now=now,
    )
    _apply_schedule_update(
        state,
        event=ScheduleEvent(
            is_correct=context.answer.is_correct,
            friction_rating=context.friction_rating,
            spacing_eligible=eligible,
        ),
        reason_codes=reason_codes,
        now=now,
    )
    return ConceptScheduleUpdate(
        concept_id=context.concept.id,
        previous_due_at=previous_due_at,
        due_at=state.due_at,
        previous_interval_days=previous_interval_days,
        interval_days=state.interval_days,
        spacing_eligible=eligible,
        reason_codes=reason_codes,
    )


def due_concept_ids(
    db: Session,
    *,
    now: datetime | None = None,
    subject_id: int | None = None,
) -> list[int]:
    due_at = now or datetime.now(UTC)
    statement = (
        select(ConceptReviewState.concept_id)
        .join(Concept)
        .where(
            Concept.status == ConceptStatus.ACTIVE,
            ConceptReviewState.due_at.is_not(None),
            ConceptReviewState.due_at <= due_at,
        )
        .order_by(ConceptReviewState.due_at, ConceptReviewState.concept_id)
    )
    if subject_id is not None:
        statement = statement.where(Concept.subject_id == subject_id)
    return list(db.scalars(statement))


def spacing_eligible(
    db: Session,
    *,
    context: ScheduleContext,
    state: ConceptReviewState,
    now: datetime,
) -> tuple[bool, list[str]]:
    reason_codes = [
        reason_code
        for blocked, reason_code in _eligibility_checks(db, context, state, now)
        if blocked
    ]
    return not reason_codes, reason_codes


def _eligibility_checks(
    db: Session,
    context: ScheduleContext,
    state: ConceptReviewState,
    now: datetime,
) -> list[tuple[bool, str]]:
    answer = context.answer
    return [
        (answer.question.status != QuestionStatus.ACTIVE, "inactive_question_not_advanced"),
        (
            answer.question.health_state in SCHEDULE_BLOCKING_HEALTH_STATES,
            "suspect_question_evidence_ignored",
        ),
        (context.concept.status != ConceptStatus.ACTIVE, "inactive_concept_not_advanced"),
        (context.role != ConceptAssignmentRole.PRIMARY, "only_secondary_evidence"),
        (not answer.is_correct, "incorrect_answer_not_advanced"),
        (answer.hint_used, "hint_used_not_advanced"),
        (_reviewed_too_recently(state, now), "same_session_repeat_not_advanced"),
        (
            _same_family_seen_recently(db, answer=answer, concept_id=context.concept.id, now=now),
            "same_question_family_repeat_not_advanced",
        ),
    ]


def _attempt_answer(db: Session, attempt_answer_id: int) -> AttemptAnswer:
    answer = db.scalar(
        select(AttemptAnswer)
        .where(AttemptAnswer.id == attempt_answer_id)
        .options(
            selectinload(AttemptAnswer.attempt),
            selectinload(AttemptAnswer.question)
            .selectinload(Question.concept_links)
            .selectinload(QuestionConcept.concept),
        )
    )
    if answer is None:
        raise ValueError(f"Unknown attempt answer id {attempt_answer_id}.")
    return answer


def _review_state_for_concept(db: Session, concept: Concept) -> ConceptReviewState:
    state = db.scalar(select(ConceptReviewState).where(ConceptReviewState.concept_id == concept.id))
    if state is not None:
        return state
    state = ConceptReviewState(
        concept=concept,
        interval_days=1,
        ease_factor=DEFAULT_EASE_FACTOR,
        successful_spaced_reviews=0,
        consecutive_spaced_correct=0,
        lapse_count=0,
    )
    db.add(state)
    return state


def _apply_schedule_update(
    state: ConceptReviewState,
    *,
    event: ScheduleEvent,
    reason_codes: list[str],
    now: datetime,
) -> None:
    if _has_schedule_mutation_blocker(reason_codes):
        return
    current = state.interval_days
    state.last_friction_rating = event.friction_rating
    if not event.is_correct:
        _apply_lapse(state, friction_rating=event.friction_rating, now=now)
        _append_once(reason_codes, "retrieval_lapse")
        return
    if event.friction_rating == SHAKY:
        state.interval_days = min(current or 1, 2)
        state.ease_factor = max(MIN_EASE_FACTOR, state.ease_factor - 0.10)
        state.due_at = _due_at(now, state.interval_days)
        state.last_review_at = now
        _append_once(reason_codes, "shaky_correct_fragile")
        return
    if not event.spacing_eligible:
        return

    if event.friction_rating == EFFORTFUL:
        state.interval_days = max(1, current)
        _append_once(reason_codes, "effortful_correct_interval_held")
    elif event.friction_rating == FLUENT:
        state.interval_days = _next_band(current)
        state.ease_factor = min(MAX_EASE_FACTOR, state.ease_factor + 0.05)
        _append_once(reason_codes, "fluent_correct_interval_advanced")

    state.successful_spaced_reviews += 1
    state.consecutive_spaced_correct += 1
    state.due_at = _due_at(now, state.interval_days)
    state.last_review_at = now


def _has_schedule_mutation_blocker(reason_codes: list[str]) -> bool:
    return any(reason_code != "incorrect_answer_not_advanced" for reason_code in reason_codes)


def _apply_lapse(
    state: ConceptReviewState,
    *,
    friction_rating: int,
    now: datetime,
) -> None:
    current = state.interval_days
    state.lapse_count += 1
    state.consecutive_spaced_correct = 0
    state.last_review_at = now
    if friction_rating == SHAKY:
        state.interval_days = 1
        state.ease_factor = max(MIN_EASE_FACTOR, state.ease_factor - 0.25)
    else:
        state.interval_days = max(1, _previous_band(current))
        state.ease_factor = max(MIN_EASE_FACTOR, state.ease_factor - 0.15)
    state.due_at = _due_at(now, state.interval_days)


def _reviewed_too_recently(state: ConceptReviewState, now: datetime) -> bool:
    if state.last_review_at is None:
        return False
    return now - state.last_review_at < timedelta(hours=MIN_SPACING_HOURS)


def _same_family_seen_recently(
    db: Session,
    *,
    answer: AttemptAnswer,
    concept_id: int,
    now: datetime,
) -> bool:
    since = now - timedelta(hours=MIN_FAMILY_REPEAT_HOURS)
    statement = (
        select(AttemptAnswer.id)
        .join(Attempt, Attempt.id == AttemptAnswer.attempt_id)
        .join(
            QuestionConcept,
            QuestionConcept.question_id == AttemptAnswer.question_id,
        )
        .where(
            AttemptAnswer.id != answer.id,
            AttemptAnswer.question_id == answer.question_id,
            QuestionConcept.concept_id == concept_id,
            Attempt.submitted_at.is_not(None),
            Attempt.submitted_at >= since,
            learner_attempt_clause(),
        )
        .limit(1)
    )
    return db.scalar(statement) is not None


def _next_band(current: int) -> int:
    for band in INTERVAL_BANDS:
        if band > current:
            return band
    return INTERVAL_BANDS[-1]


def _previous_band(current: int) -> int:
    previous = INTERVAL_BANDS[0]
    for band in INTERVAL_BANDS:
        if band >= current:
            return previous
        previous = band
    return previous


def _due_at(now: datetime, interval_days: int) -> datetime:
    return now + timedelta(days=interval_days)


def _append_once(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)
