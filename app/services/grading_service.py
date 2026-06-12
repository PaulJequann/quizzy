from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Attempt, AttemptAnswer, Concept, Question, Quiz


@dataclass(frozen=True)
class ConceptMastery:
    concept: Concept
    total_attempts: int
    correct_attempts: int
    accuracy: float
    last_missed_at: datetime | None
    status: str


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
            (choice for choice in question.choices if choice.id == selected_choice_id), None
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


def get_mastery_rows(db: Session) -> list[ConceptMastery]:
    concepts = db.scalars(
        select(Concept)
        .options(
            selectinload(Concept.subject),
            selectinload(Concept.questions)
            .selectinload(Question.attempt_answers)
            .selectinload(AttemptAnswer.attempt),
        )
        .order_by(Concept.name)
    ).all()

    rows: list[ConceptMastery] = []
    for concept in concepts:
        total_attempts = 0
        correct_attempts = 0
        last_missed_at: datetime | None = None

        for question in concept.questions:
            for answer in question.attempt_answers:
                total_attempts += 1
                if answer.is_correct:
                    correct_attempts += 1
                    continue
                missed_at = answer.attempt.submitted_at
                if missed_at and (last_missed_at is None or missed_at > last_missed_at):
                    last_missed_at = missed_at

        accuracy = round((correct_attempts / total_attempts) * 100, 2) if total_attempts else 0
        rows.append(
            ConceptMastery(
                concept=concept,
                total_attempts=total_attempts,
                correct_attempts=correct_attempts,
                accuracy=accuracy,
                last_missed_at=last_missed_at,
                status=mastery_status(accuracy),
            )
        )

    return rows


def build_concept_queue_items(rows: list[ConceptMastery]) -> list[ConceptQueueItem]:
    queue_items: list[ConceptQueueItem] = []
    for row in rows:
        if row.total_attempts == 0 or row.status == "strong":
            continue

        missed_question_ids = _missed_question_ids(row.concept)
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
                missed_question_ids=missed_question_ids,
                source_references=_queue_source_references(row.concept),
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


def _missed_question_ids(concept: Concept) -> list[int]:
    return sorted(question.id for question in concept.questions if _question_was_missed(question))


def _queue_source_references(concept: Concept) -> list[str]:
    missed_references = {
        question.source_reference
        for question in concept.questions
        if question.source_reference and _question_was_missed(question)
    }
    tagged_references = {
        question.source_reference for question in concept.questions if question.source_reference
    }
    return sorted(missed_references or tagged_references)


def _question_was_missed(question: Question) -> bool:
    return any(not answer.is_correct for answer in question.attempt_answers)


def get_concept_queue_items(db: Session) -> list[ConceptQueueItem]:
    return build_concept_queue_items(get_mastery_rows(db))


def missed_concepts_for_attempt(attempt: Attempt) -> list[Concept]:
    concepts_by_id: dict[int, Concept] = {}
    for answer in attempt.answers:
        if answer.is_correct:
            continue
        for concept in answer.question.concepts:
            concepts_by_id[concept.id] = concept
    return sorted(concepts_by_id.values(), key=lambda concept: concept.name.casefold())


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
