from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Attempt,
    AttemptAnswer,
    Concept,
    ConceptAssignmentRole,
    ConceptReviewState,
    ConceptStatus,
    Question,
    QuestionConcept,
    QuestionHealthState,
    QuestionStatus,
    Quiz,
)
from app.services.mastery_evidence_service import (
    MasteryEvidenceInput,
    capped_family_evidence,
    summarize_mastery_evidence,
)

SUCCESS_FRICTION_WEIGHT = {1: 0.45, 2: 0.85, 3: 1.15, None: 1.0}
FAILURE_FRICTION_WEIGHT = {1: 1.25, 2: 0.90, 3: 0.80, None: 1.0}
ROLE_WEIGHT = {
    ConceptAssignmentRole.PRIMARY: 1.0,
    ConceptAssignmentRole.SECONDARY: 0.30,
}
HEALTH_WEIGHT = {
    QuestionHealthState.HEALTHY: 1.0,
    QuestionHealthState.UNTESTED: 0.8,
    QuestionHealthState.NEEDS_REVIEW: 0.4,
    QuestionHealthState.SUSPECT: 0.0,
    QuestionHealthState.ARCHIVED: 0.0,
}


@dataclass(frozen=True)
class ConceptMastery:
    concept: Concept
    total_attempts: int
    correct_attempts: int
    accuracy: float
    last_missed_at: datetime | None
    status: str
    linked_question_count: int
    direct_question_count: int
    descendant_question_count: int
    primary_attempts: int
    secondary_attempts: int
    primary_question_count: int
    secondary_question_count: int
    depth: int
    mastery_state: str = "unseen"
    conservative_performance: float = 0
    coverage_score: float = 0
    confidence_score: float = 0
    due_at: datetime | None = None
    interval_days: int | None = None
    consecutive_spaced_correct: int = 0
    lapse_count: int = 0
    retrieval_friction_counts: dict[int, int] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    secondary_cues: list[str] = field(default_factory=list)
    open_review_count: int = 0
    rolled_up_question_ids: list[int] = field(default_factory=list)
    missed_question_ids: list[int] = field(default_factory=list)
    source_references: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConceptQueueItem:
    concept_id: int
    subject_id: int
    subject_name: str
    name: str
    total_attempts: int
    correct_attempts: int
    accuracy: float
    last_missed_at: datetime | None
    status: str
    mastery_state: str
    due_at: datetime | None
    conservative_performance: float
    coverage_score: float
    confidence_score: float
    reason_codes: list[str]
    missed_question_ids: list[int]
    source_references: list[str]


@dataclass(frozen=True)
class _AnswerEvidence:
    answer_id: int | None
    question_id: int
    is_correct: bool | None
    submitted_at: datetime | None
    role: ConceptAssignmentRole
    health_state: QuestionHealthState
    retrieval_friction_rating: int | None


@dataclass
class _RollupAccumulator:
    direct_question_ids: set[int] = field(default_factory=set)
    rolled_up_question_ids: set[int] = field(default_factory=set)
    primary_question_ids: set[int] = field(default_factory=set)
    secondary_question_ids: set[int] = field(default_factory=set)
    missed_question_ids: set[int] = field(default_factory=set)
    source_references: set[str] = field(default_factory=set)
    counted_answer_ids: set[int] = field(default_factory=set)
    family_success_weight: defaultdict[int, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    family_failure_weight: defaultdict[int, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    healthy_primary_question_ids: set[int] = field(default_factory=set)
    primary_seen_question_ids: set[int] = field(default_factory=set)
    latest_primary_answer_by_question: dict[int, _AnswerEvidence] = field(default_factory=dict)
    retrieval_friction_counts: defaultdict[int, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    suspect_evidence_ignored: bool = False
    total_attempts: int = 0
    correct_attempts: int = 0
    primary_attempts: int = 0
    secondary_attempts: int = 0
    last_missed_at: datetime | None = None


def submit_attempt(db: Session, quiz: Quiz, submitted_answers: dict[int, int | None]) -> Attempt:
    _validate_submission(quiz, submitted_answers)
    total_questions = len(quiz.questions)
    correct_count = 0
    attempt = Attempt(
        quiz_id=quiz.id,
        session_type=quiz.quiz_type.value,
        total_questions=total_questions,
        started_at=datetime.now(UTC),
        submitted_at=datetime.now(UTC),
    )

    for question in quiz.questions:
        selected_choice_id = submitted_answers.get(question.id)
        correct_choice = next((choice for choice in question.choices if choice.is_correct), None)
        selected_choice = next(
            (choice for choice in question.choices if choice.id == selected_choice_id),
            None,
        )
        is_correct = selected_choice_id is not None and correct_choice is not None
        is_correct = is_correct and selected_choice_id == correct_choice.id
        if is_correct:
            correct_count += 1
        attempt.answers.append(
            AttemptAnswer(
                question_id=question.id,
                selected_choice_id=selected_choice_id,
                is_correct=is_correct,
                question_stem_snapshot=question.stem,
                selected_choice_text_snapshot=selected_choice.text if selected_choice else "",
                correct_choice_text_snapshot=correct_choice.text if correct_choice else "",
                explanation_snapshot=question.explanation or "",
            )
        )

    attempt.correct_count = correct_count
    attempt.score = round((correct_count / total_questions) * 100, 2) if total_questions else 0
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def mastery_status(accuracy: float) -> str:
    if accuracy >= 90:
        return "strong"
    if accuracy >= 70:
        return "improving"
    return "weak"


def get_mastery_rows(db: Session, *, subject_id: int | None = None) -> list[ConceptMastery]:
    concepts = _active_concepts(db, subject_id=subject_id)
    accumulators = {concept.id: _RollupAccumulator() for concept in concepts}
    depths = _concept_depths(concepts)
    ancestors_by_concept_id = _ancestors_by_concept_id(concepts)
    _accumulate_assignment_rows(db, accumulators, ancestors_by_concept_id, subject_id=subject_id)
    review_states = _review_states_by_concept_id(db, subject_id=subject_id)
    return [
        _mastery_row(
            concept,
            accumulator=accumulators[concept.id],
            depth=depths[concept.id],
            review_state=review_states.get(concept.id),
        )
        for concept in concepts
    ]


def build_concept_queue_items(rows: list[ConceptMastery]) -> list[ConceptQueueItem]:
    queue_items: list[ConceptQueueItem] = []
    for row in rows:
        if not _should_queue_concept(row):
            continue
        queue_items.append(
            ConceptQueueItem(
                concept_id=row.concept.id,
                subject_id=row.concept.subject_id,
                subject_name=row.concept.subject.name,
                name=row.concept.name,
                total_attempts=row.total_attempts,
                correct_attempts=row.correct_attempts,
                accuracy=row.accuracy,
                last_missed_at=row.last_missed_at,
                status=row.status,
                mastery_state=row.mastery_state,
                due_at=row.due_at,
                conservative_performance=row.conservative_performance,
                coverage_score=row.coverage_score,
                confidence_score=row.confidence_score,
                reason_codes=row.reason_codes,
                missed_question_ids=row.missed_question_ids,
                source_references=row.source_references,
            )
        )

    status_priority = {"weak": 0, "improving": 1, "strong": 2}
    state_priority = {"learning": 0, "review_due": 1, "unseen": 2, "stable": 3, "mastered": 4}
    return sorted(
        queue_items,
        key=lambda item: (
            state_priority.get(item.mastery_state, 99),
            status_priority.get(item.status, 99),
            item.due_at or datetime.max.replace(tzinfo=UTC),
            item.accuracy,
            item.name.casefold(),
        ),
    )


def get_concept_queue_items(db: Session) -> list[ConceptQueueItem]:
    return build_concept_queue_items(get_mastery_rows(db))


def _should_queue_concept(row: ConceptMastery) -> bool:
    if row.mastery_state in {"unseen", "learning", "review_due"}:
        return True
    return row.status != "strong" and row.mastery_state != "mastered"


def missed_concepts_for_attempt(attempt: Attempt) -> list[Concept]:
    concepts_by_id: dict[int, Concept] = {}
    for answer in attempt.answers:
        concepts_by_id.update(_missed_active_concepts(answer))
    return sorted(concepts_by_id.values(), key=lambda concept: concept.name.casefold())


def mastery_focus(db: Session, *, subject_id: int | None = None, limit: int = 10) -> dict:
    rows = get_mastery_rows(db, subject_id=subject_id)
    queue_items = build_concept_queue_items(rows)
    return {
        "focus": [
            {
                "concept_id": item.concept_id,
                "subject_id": item.subject_id,
                "subject_name": item.subject_name,
                "name": item.name,
                "accuracy": item.accuracy,
                "status": item.status,
                "missed_question_ids": item.missed_question_ids,
                "source_references": item.source_references,
            }
            for item in queue_items[:limit]
        ]
    }


def subject_mastery_tree(db: Session, *, subject_id: int) -> dict:
    rows = get_mastery_rows(db, subject_id=subject_id)
    rows_by_parent_id: dict[int | None, list[ConceptMastery]] = {}
    for row in rows:
        rows_by_parent_id.setdefault(row.concept.parent_concept_id, []).append(row)
    return {
        "subject_id": subject_id,
        "concepts": [
            _tree_node(row, rows_by_parent_id)
            for row in sorted(
                rows_by_parent_id.get(None, []),
                key=lambda item: item.concept.name.casefold(),
            )
        ],
    }


def letter_grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def passed(score: float) -> bool:
    return score >= 70


def _active_concepts(db: Session, *, subject_id: int | None) -> list[Concept]:
    statement = (
        select(Concept)
        .where(Concept.status == ConceptStatus.ACTIVE)
        .options(selectinload(Concept.subject))
        .order_by(Concept.name)
    )
    if subject_id is not None:
        statement = statement.where(Concept.subject_id == subject_id)
    return list(db.scalars(statement))


def _review_states_by_concept_id(
    db: Session,
    *,
    subject_id: int | None,
) -> dict[int, ConceptReviewState]:
    statement = select(ConceptReviewState).join(Concept)
    if subject_id is not None:
        statement = statement.where(Concept.subject_id == subject_id)
    return {state.concept_id: state for state in db.scalars(statement)}


def _concept_depths(concepts: list[Concept]) -> dict[int, int]:
    concepts_by_id = {concept.id: concept for concept in concepts}
    return {concept.id: len(_ancestor_ids(concept, concepts_by_id)) for concept in concepts}


def _ancestors_by_concept_id(concepts: list[Concept]) -> dict[int, list[int]]:
    concepts_by_id = {concept.id: concept for concept in concepts}
    return {
        concept.id: [ancestor.id for ancestor in _ancestor_ids(concept, concepts_by_id)]
        for concept in concepts
    }


def _accumulate_assignment_rows(
    db: Session,
    accumulators: dict[int, _RollupAccumulator],
    ancestors_by_concept_id: dict[int, list[int]],
    *,
    subject_id: int | None,
) -> None:
    if not accumulators:
        return
    statement = (
        select(
            QuestionConcept.concept_id,
            QuestionConcept.question_id,
            QuestionConcept.role,
            Question.source_reference,
            Question.health_state,
            AttemptAnswer.id,
            AttemptAnswer.is_correct,
            AttemptAnswer.retrieval_friction_rating,
            Attempt.submitted_at,
        )
        .join(Question, Question.id == QuestionConcept.question_id)
        .outerjoin(AttemptAnswer, AttemptAnswer.question_id == Question.id)
        .outerjoin(Attempt, Attempt.id == AttemptAnswer.attempt_id)
        .where(
            Question.status == QuestionStatus.ACTIVE,
            QuestionConcept.concept_id.in_(accumulators.keys()),
        )
        .execution_options(include_exam_content=True)
    )
    if subject_id is not None:
        statement = statement.where(Question.subject_id == subject_id)
    for row in db.execute(statement):
        _accumulate_assignment_row(accumulators, ancestors_by_concept_id, row)


def _accumulate_assignment_row(
    accumulators: dict[int, _RollupAccumulator],
    ancestors_by_concept_id: dict[int, list[int]],
    row,
) -> None:
    (
        concept_id,
        question_id,
        role,
        source_reference,
        health_state,
        answer_id,
        is_correct,
        retrieval_friction_rating,
        submitted_at,
    ) = row
    for rollup_concept_id in ancestors_by_concept_id.get(concept_id, [concept_id]):
        accumulator = accumulators[rollup_concept_id]
        _accumulate_question_identity(
            accumulator,
            is_direct=rollup_concept_id == concept_id,
            question_id=question_id,
            source_reference=source_reference,
        )
        evidence = _AnswerEvidence(
            answer_id=answer_id,
            question_id=question_id,
            is_correct=is_correct,
            submitted_at=submitted_at,
            role=role,
            health_state=health_state,
            retrieval_friction_rating=retrieval_friction_rating,
        )
        _accumulate_role(accumulator, evidence)
        _accumulate_answer_evidence(accumulator, evidence)


def _missed_active_concepts(answer: AttemptAnswer) -> dict[int, Concept]:
    if answer.is_correct:
        return {}
    return {
        concept.id: concept
        for concept in answer.question.concepts
        if concept.status == ConceptStatus.ACTIVE
    }


def _ancestor_ids(concept: Concept, concepts_by_id: dict[int, Concept]) -> list[Concept]:
    ancestors = [concept]
    parent_id = concept.parent_concept_id
    while parent_id is not None and parent_id in concepts_by_id:
        parent = concepts_by_id[parent_id]
        ancestors.append(parent)
        parent_id = parent.parent_concept_id
    return ancestors


def _accumulate_question_identity(
    accumulator: _RollupAccumulator,
    *,
    is_direct: bool,
    question_id: int,
    source_reference: str | None,
) -> None:
    if is_direct:
        accumulator.direct_question_ids.add(question_id)
    accumulator.rolled_up_question_ids.add(question_id)
    if source_reference:
        accumulator.source_references.add(source_reference)


def _accumulate_answer_evidence(
    accumulator: _RollupAccumulator,
    evidence: _AnswerEvidence,
) -> None:
    if evidence.is_correct is None:
        return
    if evidence.answer_id is None:
        return
    if evidence.answer_id in accumulator.counted_answer_ids:
        return
    accumulator.counted_answer_ids.add(evidence.answer_id)
    _accumulate_weighted_evidence(accumulator, evidence)
    _accumulate_latest_primary_answer(accumulator, evidence)
    _accumulate_retrieval_friction(accumulator, evidence.retrieval_friction_rating)
    accumulator.total_attempts += 1
    if evidence.is_correct:
        accumulator.correct_attempts += 1
        return
    accumulator.missed_question_ids.add(evidence.question_id)
    if _is_newer_miss(accumulator, evidence.submitted_at):
        accumulator.last_missed_at = evidence.submitted_at


def _is_newer_miss(
    accumulator: _RollupAccumulator,
    submitted_at: datetime | None,
) -> bool:
    if submitted_at is None:
        return False
    if accumulator.last_missed_at is None:
        return True
    return submitted_at > accumulator.last_missed_at


def _accumulate_role(
    accumulator: _RollupAccumulator,
    evidence: _AnswerEvidence,
) -> None:
    if evidence.role == ConceptAssignmentRole.PRIMARY:
        accumulator.primary_question_ids.add(evidence.question_id)
        if HEALTH_WEIGHT[evidence.health_state] > 0:
            accumulator.healthy_primary_question_ids.add(evidence.question_id)
        if evidence.is_correct is not None:
            accumulator.primary_attempts += 1
            accumulator.primary_seen_question_ids.add(evidence.question_id)
        return
    accumulator.secondary_question_ids.add(evidence.question_id)
    if evidence.is_correct is not None:
        accumulator.secondary_attempts += 1


def _accumulate_weighted_evidence(
    accumulator: _RollupAccumulator,
    evidence: _AnswerEvidence,
) -> None:
    base_weight = ROLE_WEIGHT[evidence.role] * HEALTH_WEIGHT[evidence.health_state]
    if base_weight == 0:
        accumulator.suspect_evidence_ignored = True
        return
    if evidence.is_correct:
        accumulator.family_success_weight[evidence.question_id] += (
            base_weight * SUCCESS_FRICTION_WEIGHT[evidence.retrieval_friction_rating]
        )
        return
    accumulator.family_failure_weight[evidence.question_id] += (
        base_weight * FAILURE_FRICTION_WEIGHT[evidence.retrieval_friction_rating]
    )


def _accumulate_latest_primary_answer(
    accumulator: _RollupAccumulator,
    evidence: _AnswerEvidence,
) -> None:
    if evidence.role != ConceptAssignmentRole.PRIMARY:
        return
    latest = accumulator.latest_primary_answer_by_question.get(evidence.question_id)
    if latest is None or _evidence_is_newer(evidence, latest):
        accumulator.latest_primary_answer_by_question[evidence.question_id] = evidence


def _accumulate_retrieval_friction(
    accumulator: _RollupAccumulator,
    friction_rating: int | None,
) -> None:
    if friction_rating is not None:
        accumulator.retrieval_friction_counts[friction_rating] += 1


def _evidence_is_newer(candidate: _AnswerEvidence, current: _AnswerEvidence) -> bool:
    return _evidence_sort_key(candidate) > _evidence_sort_key(current)


def _evidence_sort_key(evidence: _AnswerEvidence) -> tuple[datetime, int]:
    submitted_at = evidence.submitted_at or datetime.min.replace(tzinfo=UTC)
    return submitted_at, evidence.answer_id or 0


def _mastery_row(
    concept: Concept,
    *,
    accumulator: _RollupAccumulator,
    depth: int,
    review_state: ConceptReviewState | None,
) -> ConceptMastery:
    accuracy = (
        round((accumulator.correct_attempts / accumulator.total_attempts) * 100, 2)
        if accumulator.total_attempts
        else 0
    )
    effective_success, effective_failure = capped_family_evidence(
        accumulator.family_success_weight,
        accumulator.family_failure_weight,
    )
    evidence_summary = summarize_mastery_evidence(
        MasteryEvidenceInput(
            effective_success=effective_success,
            effective_failure=effective_failure,
            primary_attempts=accumulator.primary_attempts,
            secondary_attempts=accumulator.secondary_attempts,
            healthy_primary_question_count=len(accumulator.healthy_primary_question_ids),
            primary_seen_question_count=len(accumulator.primary_seen_question_ids),
            unresolved_primary_miss_count=_unresolved_primary_miss_count(accumulator),
            suspect_evidence_ignored=accumulator.suspect_evidence_ignored,
            due_at=review_state.due_at if review_state else None,
            interval_days=review_state.interval_days if review_state else None,
            consecutive_spaced_correct=(
                review_state.consecutive_spaced_correct if review_state else 0
            ),
        )
    )
    return ConceptMastery(
        concept=concept,
        total_attempts=accumulator.total_attempts,
        correct_attempts=accumulator.correct_attempts,
        accuracy=accuracy,
        last_missed_at=accumulator.last_missed_at,
        status=evidence_summary.proficiency_band,
        linked_question_count=len(accumulator.rolled_up_question_ids),
        direct_question_count=len(accumulator.direct_question_ids),
        descendant_question_count=(
            len(accumulator.rolled_up_question_ids) - len(accumulator.direct_question_ids)
        ),
        primary_attempts=accumulator.primary_attempts,
        secondary_attempts=accumulator.secondary_attempts,
        primary_question_count=len(accumulator.primary_question_ids),
        secondary_question_count=len(accumulator.secondary_question_ids),
        depth=depth,
        mastery_state=evidence_summary.mastery_state,
        conservative_performance=round(evidence_summary.conservative_performance * 100, 2),
        coverage_score=round(evidence_summary.coverage_score * 100, 2),
        confidence_score=round(evidence_summary.confidence_score * 100, 2),
        due_at=review_state.due_at if review_state else None,
        interval_days=review_state.interval_days if review_state else None,
        consecutive_spaced_correct=(review_state.consecutive_spaced_correct if review_state else 0),
        lapse_count=review_state.lapse_count if review_state else 0,
        retrieval_friction_counts=dict(accumulator.retrieval_friction_counts),
        reason_codes=evidence_summary.reason_codes,
        secondary_cues=_secondary_cues(evidence_summary.reason_codes, review_state),
        rolled_up_question_ids=sorted(accumulator.rolled_up_question_ids),
        missed_question_ids=sorted(accumulator.missed_question_ids),
        source_references=sorted(accumulator.source_references),
    )


def _unresolved_primary_miss_count(accumulator: _RollupAccumulator) -> int:
    return sum(
        1
        for answer in accumulator.latest_primary_answer_by_question.values()
        if answer.is_correct is False
    )


def _secondary_cues(
    reason_codes: list[str],
    review_state: ConceptReviewState | None,
) -> list[str]:
    cues = [
        label for reason_code, label in _reason_code_labels().items() if reason_code in reason_codes
    ]
    if review_state and review_state.last_friction_rating == 1:
        cues.append("Last retrieval felt shaky")
    if future_due_label := _future_review_due_label(review_state, reason_codes):
        cues.append(future_due_label)
    return cues[:3]


def _future_review_due_label(
    review_state: ConceptReviewState | None,
    reason_codes: list[str],
) -> str | None:
    if review_state is None:
        return None
    if review_state.due_at is None:
        return None
    if "review_due" in reason_codes:
        return None
    return f"Next review {review_state.due_at.strftime('%b %-d')}"


def _reason_code_labels() -> dict[str, str]:
    return {
        "review_due": "Due for spaced retrieval",
        "unseen": "New",
        "only_secondary_evidence": "Only seen indirectly",
        "low_coverage": "Needs more primary-question coverage",
        "unresolved_primary_miss": "Unresolved primary miss",
        "needs_fluent_spaced_retrieval": "Needs fluent spaced retrieval",
    }


def _tree_node(
    row: ConceptMastery,
    rows_by_parent_id: dict[int | None, list[ConceptMastery]],
) -> dict:
    children = sorted(
        rows_by_parent_id.get(row.concept.id, []),
        key=lambda item: item.concept.name.casefold(),
    )
    return {
        "id": row.concept.id,
        "name": row.concept.name,
        "status": row.concept.status.value,
        "accuracy": row.accuracy,
        "attempts": row.total_attempts,
        "correct_attempts": row.correct_attempts,
        "linked_question_count": row.linked_question_count,
        "primary_question_count": row.primary_question_count,
        "secondary_question_count": row.secondary_question_count,
        "children": [_tree_node(child, rows_by_parent_id) for child in children],
    }


def _validate_submission(quiz: Quiz, submitted_answers: dict[int, int | None]) -> None:
    valid_choices = {
        question.id: {None, *(choice.id for choice in question.choices)}
        for question in quiz.questions
    }
    for question_id, choice_id in submitted_answers.items():
        if question_id not in valid_choices:
            raise ValueError("Unknown question in submission")
        if choice_id not in valid_choices[question_id]:
            raise ValueError("Selected choice does not belong to the question")
