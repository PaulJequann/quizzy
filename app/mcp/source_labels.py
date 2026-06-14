from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceDocument
from app.services.generation_service import GeneratedQuestionInput


def for_questions(db: Session, questions: list[GeneratedQuestionInput]) -> list[str]:
    document_ids = _source_document_ids(questions)
    reference_labels = [
        label for question in questions if (label := _source_reference_label(question))
    ]
    if not document_ids:
        return reference_labels
    titles = list(
        db.scalars(select(SourceDocument.title).where(SourceDocument.id.in_(document_ids))).all()
    )
    return [*titles, *reference_labels]


def _source_document_ids(questions: list[GeneratedQuestionInput]) -> list[int]:
    return sorted(
        {question.source_document_id for question in questions if question.source_document_id}
    )


def _source_reference_label(question: GeneratedQuestionInput) -> str | None:
    if not question.source_reference:
        return None
    path = Path(question.source_reference)
    return path.stem or question.source_reference
