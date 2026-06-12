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


def submit_attempt(db: Session, quiz: Quiz, submitted_answers: dict[int, int | None]) -> Attempt:
    total_questions = len(quiz.questions)
    correct_count = 0
    attempt = Attempt(
        quiz_id=quiz.id,
        total_questions=total_questions,
        started_at=datetime.now(UTC),
        submitted_at=datetime.now(UTC),
    )

    for question in quiz.questions:
        selected_choice_id = submitted_answers.get(question.id)
        correct_choice = next((choice for choice in question.choices if choice.is_correct), None)
        is_correct = selected_choice_id is not None and correct_choice is not None
        is_correct = is_correct and selected_choice_id == correct_choice.id
        if is_correct:
            correct_count += 1
        attempt.answers.append(
            AttemptAnswer(
                question_id=question.id,
                selected_choice_id=selected_choice_id,
                is_correct=is_correct,
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
            selectinload(Concept.questions)
            .selectinload(Question.attempt_answers)
            .selectinload(AttemptAnswer.attempt)
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
