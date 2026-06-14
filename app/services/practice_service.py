from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AttemptAnswer,
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionHealthState,
    QuestionStatus,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.services.grading_service import get_concept_queue_items

DEFAULT_PRACTICE_COUNT = 10
QUESTION_DIFFICULTIES = (Difficulty.BEGINNER, Difficulty.INTERMEDIATE, Difficulty.ADVANCED)


@dataclass(frozen=True)
class PracticeSelection:
    question: Question
    reason: str
    score: float


@dataclass(frozen=True)
class PracticeAssemblyInput:
    subject_id: int
    count: int = DEFAULT_PRACTICE_COUNT
    strategy: str = "random_subject"
    concept_ids: list[int] | None = None
    difficulty: Difficulty | None = None
    source_document_id: int | None = None


def assemble_practice_session(
    db: Session,
    data: PracticeAssemblyInput,
) -> Quiz:
    subject = db.get(Subject, data.subject_id)
    if subject is None:
        raise ValueError(f"Unknown subject id {data.subject_id}")

    candidates = _candidate_questions(
        db,
        data,
    )
    selections = _select_questions(candidates, data)
    if not selections:
        raise ValueError("No active questions are available for practice.")

    quiz = Quiz(
        title=_practice_title(subject, data.strategy),
        description="Disposable practice assembled from the subject question bank.",
        primary_subject=subject,
        difficulty=data.difficulty or Difficulty.MIXED,
        quiz_type=QuizType.PRACTICE,
        status=QuizStatus.PUBLISHED,
        assembly_strategy=data.strategy,
        assembly_params_json=json.dumps(
            {
                "subject_id": subject.id,
                "count": data.count,
                "concept_ids": data.concept_ids or [],
                "difficulty": data.difficulty.value if data.difficulty else None,
                "source_document_id": data.source_document_id,
            },
            sort_keys=True,
        ),
        is_disposable=True,
    )
    for order_index, selection in enumerate(selections, start=1):
        quiz.question_links.append(
            QuizQuestion(
                question=selection.question,
                order_index=order_index,
                selection_reason=selection.reason,
                selection_score=selection.score,
            )
        )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


def assemble_mastery_review(
    db: Session,
    *,
    subject_id: int | None = None,
    count: int = DEFAULT_PRACTICE_COUNT,
) -> Quiz:
    concept_ids = [
        item.concept_id
        for item in get_concept_queue_items(db)
        if subject_id is None or item.subject_id == subject_id
    ]
    if not concept_ids:
        raise ValueError("No weak or improving concepts are ready for practice.")
    resolved_subject_id = subject_id or _subject_id_for_concepts(db, concept_ids)
    return assemble_practice_session(
        db,
        PracticeAssemblyInput(
            subject_id=resolved_subject_id,
            count=count,
            strategy="mastery_review",
            concept_ids=concept_ids,
        ),
    )


def assemble_concept_drill(db: Session, *, concept_ids: list[int], count: int) -> Quiz:
    if not concept_ids:
        raise ValueError("At least one concept is required for a concept drill.")
    subject_id = _subject_id_for_concepts(db, concept_ids)
    return assemble_practice_session(
        db,
        PracticeAssemblyInput(
            subject_id=subject_id,
            count=count,
            strategy="concept_drill",
            concept_ids=concept_ids,
        ),
    )


def _candidate_questions(
    db: Session,
    data: PracticeAssemblyInput,
) -> list[Question]:
    statement = (
        select(Question)
        .where(
            Question.subject_id == data.subject_id,
            Question.status == QuestionStatus.ACTIVE,
            Question.health_state != QuestionHealthState.ARCHIVED,
            Question.health_state != QuestionHealthState.SUSPECT,
        )
        .options(
            selectinload(Question.choices),
            selectinload(Question.concept_links).selectinload(QuestionConcept.concept),
            selectinload(Question.attempt_answers).selectinload(AttemptAnswer.attempt),
        )
        .order_by(Question.id)
    )
    if data.difficulty is not None:
        statement = statement.where(Question.difficulty == data.difficulty)
    if data.source_document_id is not None:
        statement = statement.where(Question.source_document_id == data.source_document_id)
    if data.concept_ids:
        statement = statement.join(QuestionConcept).where(
            QuestionConcept.concept_id.in_(_expanded_concept_ids(db, data.concept_ids))
        )
    return list(db.scalars(statement).unique())


def _select_questions(
    candidates: list[Question],
    data: PracticeAssemblyInput,
) -> list[PracticeSelection]:
    if data.strategy == "difficulty_balanced":
        return _difficulty_balanced_selection(candidates, data)

    ranked = sorted(
        (_selection_for_question(question, data) for question in candidates),
        key=lambda selection: (-selection.score, selection.question.id),
    )
    return ranked[: max(data.count, 1)]


def _selection_for_question(
    question: Question,
    data: PracticeAssemblyInput,
) -> PracticeSelection:
    health_score = _health_selection_score(question)
    score = health_score + _strategy_score(question, data)
    return PracticeSelection(
        question=question,
        reason=_selection_reason(question, data.strategy),
        score=score,
    )


def _difficulty_balanced_selection(
    candidates: list[Question],
    data: PracticeAssemblyInput,
) -> list[PracticeSelection]:
    grouped = _group_selections_by_difficulty(candidates, data)
    return _round_robin_difficulties(grouped, max(data.count, 1))


def _group_selections_by_difficulty(
    candidates: list[Question],
    data: PracticeAssemblyInput,
) -> dict[Difficulty, list[PracticeSelection]]:
    grouped: dict[Difficulty, list[PracticeSelection]] = {
        difficulty: [
            _selection_for_question(question, data)
            for question in candidates
            if question.difficulty == difficulty
        ]
        for difficulty in QUESTION_DIFFICULTIES
    }
    for selections in grouped.values():
        selections.sort(key=lambda selection: (-selection.score, selection.question.id))
    return grouped


def _round_robin_difficulties(
    grouped: dict[Difficulty, list[PracticeSelection]],
    count: int,
) -> list[PracticeSelection]:
    selected: list[PracticeSelection] = []
    while _has_selection_capacity(selected, grouped, count):
        selected.extend(_next_difficulty_round(grouped, count - len(selected)))
    return selected


def _has_selection_capacity(
    selected: list[PracticeSelection],
    grouped: dict[Difficulty, list[PracticeSelection]],
    count: int,
) -> bool:
    return len(selected) < count and any(grouped.values())


def _next_difficulty_round(
    grouped: dict[Difficulty, list[PracticeSelection]],
    remaining: int,
) -> list[PracticeSelection]:
    round_selection: list[PracticeSelection] = []
    for difficulty in QUESTION_DIFFICULTIES:
        if grouped[difficulty] and len(round_selection) < remaining:
            round_selection.append(grouped[difficulty].pop(0))
    return round_selection


def _health_selection_score(question: Question) -> float:
    if question.health_state == QuestionHealthState.HEALTHY:
        return 100
    if question.health_state == QuestionHealthState.UNTESTED:
        return 80
    if question.health_state == QuestionHealthState.NEEDS_REVIEW:
        return 20
    return 0


def _strategy_score(question: Question, data: PracticeAssemblyInput) -> float:
    if data.strategy == "mastery_review" and _has_priority_concept(question, data.concept_ids):
        return 50 + _recency_score(question)
    if data.strategy == "concept_drill":
        return _concept_drill_score(question, data.concept_ids) + _recency_score(question)
    if data.strategy == "spaced_review":
        return _spaced_review_score(question)
    return _recency_score(question)


def _has_priority_concept(question: Question, concept_ids: list[int] | None) -> bool:
    if not concept_ids:
        return False
    return any(
        link.concept_id in concept_ids and link.role == ConceptAssignmentRole.PRIMARY
        for link in question.concept_links
    )


def _concept_drill_score(question: Question, concept_ids: list[int] | None) -> float:
    if not concept_ids:
        return 0
    if any(
        link.concept_id in concept_ids and link.role == ConceptAssignmentRole.PRIMARY
        for link in question.concept_links
    ):
        return 40
    if any(link.concept_id in concept_ids for link in question.concept_links):
        return 8
    return 0


def _expanded_concept_ids(db: Session, concept_ids: list[int]) -> list[int]:
    concepts = list(
        db.scalars(
            select(Concept).where(
                Concept.status == ConceptStatus.ACTIVE,
                Concept.id.in_(concept_ids) | Concept.parent_concept_id.in_(concept_ids),
            )
        )
    )
    all_ids = {concept.id for concept in concepts}
    frontier = {concept.id for concept in concepts if concept.id not in concept_ids}
    while frontier:
        children = list(
            db.scalars(
                select(Concept.id).where(
                    Concept.status == ConceptStatus.ACTIVE,
                    Concept.parent_concept_id.in_(frontier),
                )
            )
        )
        frontier = set(children) - all_ids
        all_ids.update(children)
    return sorted(all_ids | set(concept_ids))


def _recency_score(question: Question) -> float:
    return 10 if not question.attempt_answers else 0


def _spaced_review_score(question: Question) -> float:
    if not question.attempt_answers:
        return 20
    missed_count = sum(not answer.is_correct for answer in question.attempt_answers)
    return 10 + missed_count


def _selection_reason(question: Question, strategy: str) -> str:
    if question.health_state == QuestionHealthState.UNTESTED:
        return f"{strategy}: exploration slot for an untested active question"
    if question.health_state == QuestionHealthState.NEEDS_REVIEW:
        return f"{strategy}: included despite open health review because the pool is limited"
    return f"{strategy}: selected from active healthy question bank"


def _practice_title(subject: Subject, strategy: str) -> str:
    labels = {
        "concept_drill": "Concept Drill",
        "mastery_review": "Mastery Review",
        "spaced_review": "Spaced Review",
        "difficulty_balanced": "Difficulty Practice",
        "source_based": "Source Practice",
    }
    return f"{subject.name} {labels.get(strategy, 'Quick Practice')}"


def _subject_id_for_concepts(db: Session, concept_ids: list[int]) -> int:
    subject_ids = set(
        db.scalars(select(Concept.subject_id).where(Concept.id.in_(concept_ids))).all()
    )
    if not subject_ids:
        raise ValueError("Unknown concept ids.")
    if len(subject_ids) > 1:
        raise ValueError("Concept drill requires concepts from one subject.")
    return subject_ids.pop()
