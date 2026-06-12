from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Attempt, AttemptAnswer, Quiz


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
