from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Question, QuestionHealthState, QuestionStatus

if TYPE_CHECKING:
    from app.services.grading_service import ConceptMastery

PRACTICE_ELIGIBLE = (
    Question.exam_only.is_(False),
    Question.status == QuestionStatus.ACTIVE,
    Question.health_state.not_in([QuestionHealthState.ARCHIVED, QuestionHealthState.SUSPECT]),
)


def scope_mastery_rows(db: Session, rows: list[ConceptMastery]) -> list[ConceptMastery]:
    question_ids = set().union(*(row.rolled_up_question_ids for row in rows))
    if not question_ids:
        return rows
    # Mastery evidence includes exam answers, so ids leave only through the session's own scope.
    visible = set(db.scalars(select(Question.id).where(Question.id.in_(question_ids))))
    practice = set(
        db.scalars(select(Question.id).where(Question.id.in_(question_ids), *PRACTICE_ELIGIBLE))
    )
    return [
        replace(
            row,
            missed_question_ids=[id_ for id_ in row.missed_question_ids if id_ in visible],
            practice_question_count=len(practice.intersection(row.rolled_up_question_ids)),
        )
        for row in rows
    ]


def reviewable_rows(rows: list[ConceptMastery]) -> list[ConceptMastery]:
    return [row for row in rows if row.practice_question_count]
