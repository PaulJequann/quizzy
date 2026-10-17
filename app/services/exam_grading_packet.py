from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Question, QuestionConcept
from app.services.exam_grades import Grade
from app.services.exam_paper import PaperQuestion
from app.services.exam_results import ExamRecord
from app.services.grading_service import ConceptMastery, get_mastery_rows


def grading_packet(db: Session, record: ExamRecord) -> dict[str, Any]:
    sources = _source_questions(db, [question.question_id for question in record.paper.questions])
    questions = [
        _packet_question(record, question, sources.get(question.question_id))
        for question in record.paper.questions
    ]
    concept_ids = {concept["concept_id"] for item in questions for concept in item["concepts"]}
    return {
        "exam_id": record.id,
        "title": record.title,
        "review_note": record.review_note,
        "questions": questions,
        "mastery": [
            _mastery_row(row) for row in get_mastery_rows(db) if row.concept.id in concept_ids
        ],
    }


def grade_payload(key: int, grade: Grade) -> dict[str, Any]:
    return {
        "question_key": key,
        "key_points_met": list(grade.key_points_met),
        "feedback": grade.feedback,
        "misconception": grade.misconception,
    }


def _source_questions(db: Session, question_ids: list[int]) -> dict[int, Question]:
    questions = db.scalars(
        select(Question)
        .options(selectinload(Question.concept_links).selectinload(QuestionConcept.concept))
        .where(Question.id.in_(question_ids))
    )
    return {question.id: question for question in questions}


def _packet_question(
    record: ExamRecord, question: PaperQuestion, source: Question | None
) -> dict[str, Any]:
    grade = record.grades.get(question.key)
    return {
        **question.model_dump(mode="json"),
        "answer": record.responses.get(question.key),
        "grade": grade_payload(question.key, grade) if grade else None,
        **_source_payload(source),
    }


def _source_payload(source: Question | None) -> dict[str, Any]:
    if source is None:
        return {"source_reference": None, "source_excerpt": None, "concepts": []}
    return {
        "source_reference": source.source_reference,
        "source_excerpt": source.source_excerpt,
        "concepts": [
            {"concept_id": link.concept.id, "name": link.concept.name, "role": link.role.value}
            for link in source.concept_links
        ],
    }


def _mastery_row(row: ConceptMastery) -> dict[str, Any]:
    return {
        "concept_id": row.concept.id,
        "name": row.concept.name,
        "mastery_state": row.mastery_state,
        "status": row.status,
        "accuracy": row.accuracy,
        "total_attempts": row.total_attempts,
        "correct_attempts": row.correct_attempts,
    }
