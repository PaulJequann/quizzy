from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Attempt,
    AttemptAnswer,
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Question,
    QuestionConcept,
    QuestionStatus,
    Quiz,
)


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
    missed_question_ids: list[int]
    source_references: list[str]


@dataclass(frozen=True)
class _AnswerEvidence:
    answer_id: int | None
    question_id: int
    is_correct: bool | None
    submitted_at: datetime | None


@dataclass
class _RollupAccumulator:
    direct_question_ids: set[int] = field(default_factory=set)
    rolled_up_question_ids: set[int] = field(default_factory=set)
    primary_question_ids: set[int] = field(default_factory=set)
    secondary_question_ids: set[int] = field(default_factory=set)
    missed_question_ids: set[int] = field(default_factory=set)
    source_references: set[str] = field(default_factory=set)
    counted_answer_ids: set[int] = field(default_factory=set)
    total_attempts: int = 0
    correct_attempts: int = 0
    primary_attempts: int = 0
    secondary_attempts: int = 0
    last_missed_at: datetime | None = None


def submit_attempt(db: Session, quiz: Quiz, submitted_answers: dict[int, int | None]) -> Attempt:
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
    return [
        _mastery_row(
            concept,
            accumulator=accumulators[concept.id],
            depth=depths[concept.id],
        )
        for concept in concepts
    ]


def build_concept_queue_items(rows: list[ConceptMastery]) -> list[ConceptQueueItem]:
    queue_items: list[ConceptQueueItem] = []
    for row in rows:
        if row.total_attempts == 0 or row.status == "strong":
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
                missed_question_ids=row.missed_question_ids,
                source_references=row.source_references,
            )
        )

    status_priority = {"weak": 0, "improving": 1}
    return sorted(
        queue_items,
        key=lambda item: (
            status_priority.get(item.status, 99),
            item.accuracy,
            item.name.casefold(),
        ),
    )


def get_concept_queue_items(db: Session) -> list[ConceptQueueItem]:
    return build_concept_queue_items(get_mastery_rows(db))


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
            AttemptAnswer.id,
            AttemptAnswer.is_correct,
            Attempt.submitted_at,
        )
        .join(Question, Question.id == QuestionConcept.question_id)
        .outerjoin(AttemptAnswer, AttemptAnswer.question_id == Question.id)
        .outerjoin(Attempt, Attempt.id == AttemptAnswer.attempt_id)
        .where(
            Question.status == QuestionStatus.ACTIVE,
            QuestionConcept.concept_id.in_(accumulators.keys()),
        )
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
    concept_id, question_id, role, source_reference, answer_id, is_correct, submitted_at = row
    for rollup_concept_id in ancestors_by_concept_id.get(concept_id, [concept_id]):
        accumulator = accumulators[rollup_concept_id]
        _accumulate_question_identity(
            accumulator,
            is_direct=rollup_concept_id == concept_id,
            question_id=question_id,
            source_reference=source_reference,
        )
        _accumulate_role(accumulator, role, question_id, is_correct)
        _accumulate_answer_evidence(
            accumulator,
            _AnswerEvidence(
                answer_id=answer_id,
                question_id=question_id,
                is_correct=is_correct,
                submitted_at=submitted_at,
            ),
        )


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
    role: ConceptAssignmentRole,
    question_id: int,
    is_correct: bool | None,
) -> None:
    if role == ConceptAssignmentRole.PRIMARY:
        accumulator.primary_question_ids.add(question_id)
        if is_correct is not None:
            accumulator.primary_attempts += 1
        return
    accumulator.secondary_question_ids.add(question_id)
    if is_correct is not None:
        accumulator.secondary_attempts += 1


def _mastery_row(
    concept: Concept,
    *,
    accumulator: _RollupAccumulator,
    depth: int,
) -> ConceptMastery:
    accuracy = (
        round((accumulator.correct_attempts / accumulator.total_attempts) * 100, 2)
        if accumulator.total_attempts
        else 0
    )
    return ConceptMastery(
        concept=concept,
        total_attempts=accumulator.total_attempts,
        correct_attempts=accumulator.correct_attempts,
        accuracy=accuracy,
        last_missed_at=accumulator.last_missed_at,
        status=mastery_status(accuracy),
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
        rolled_up_question_ids=sorted(accumulator.rolled_up_question_ids),
        missed_question_ids=sorted(accumulator.missed_question_ids),
        source_references=sorted(accumulator.source_references),
    )


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
