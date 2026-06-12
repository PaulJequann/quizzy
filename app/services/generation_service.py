from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Concept,
    Difficulty,
    Question,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.services import question_bank_service
from app.services.question_bank_service import BatchMetadata, QuestionBankCreateInput
from app.services.quiz_service import get_or_create_subject, slugify
from app.services.settings_service import get_default_difficulty


class GeneratedChoiceInput(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False


class GeneratedQuestionInput(BaseModel):
    stem: str = Field(min_length=1)
    choices: list[GeneratedChoiceInput] = Field(min_length=4, max_length=4)
    explanation: str | None = None
    difficulty: Difficulty | None = None
    concept_names: list[str] = Field(default_factory=list)
    source_document_id: int | None = None
    source_reference: str | None = None
    source_excerpt: str | None = None


@dataclass(frozen=True)
class GeneratedQuizCreateInput:
    title: str
    subject_name: str
    description: str = ""
    difficulty: Difficulty | None = None
    status: QuizStatus = QuizStatus.DRAFT


def default_difficulty(db: Session) -> Difficulty:
    return get_default_difficulty(db)


def list_subjects(db: Session) -> list[dict[str, Any]]:
    subjects = db.scalars(select(Subject).order_by(Subject.name)).all()
    return [
        {
            "id": subject.id,
            "name": subject.name,
            "slug": subject.slug,
            "description": subject.description,
        }
        for subject in subjects
    ]


def list_concepts(db: Session, *, subject_name: str | None = None) -> list[dict[str, Any]]:
    statement = select(Concept).options(selectinload(Concept.subject)).order_by(Concept.name)
    if subject_name:
        subject = db.scalar(select(Subject).where(Subject.slug == slugify(subject_name)))
        if subject is None:
            return []
        statement = statement.where(Concept.subject == subject)
    return [
        {
            "id": concept.id,
            "name": concept.name,
            "slug": concept.slug,
            "subject_id": concept.subject_id,
            "subject": concept.subject.name,
            "description": concept.description,
        }
        for concept in db.scalars(statement)
    ]


def create_subject(db: Session, *, name: str, description: str = "") -> dict[str, Any]:
    subject = get_or_create_subject(db, name)
    if description.strip():
        subject.description = description.strip()
    db.commit()
    db.refresh(subject)
    return {
        "id": subject.id,
        "name": subject.name,
        "slug": subject.slug,
        "description": subject.description,
    }


def create_generated_quiz(
    db: Session,
    quiz_input: GeneratedQuizCreateInput,
) -> Quiz:
    subject = get_or_create_subject(db, quiz_input.subject_name)
    quiz = Quiz(
        title=quiz_input.title.strip(),
        description=quiz_input.description.strip(),
        primary_subject=subject,
        difficulty=quiz_input.difficulty or default_difficulty(db),
        quiz_type=QuizType.GENERATED,
        status=quiz_input.status,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


def create_generated_quiz_with_questions(
    db: Session,
    quiz_input: GeneratedQuizCreateInput,
    questions: list[GeneratedQuestionInput],
) -> dict[str, Any]:
    question_bank_service.validate_question_batch(db, questions)
    quiz = create_generated_quiz(db, quiz_input)
    add_questions_to_quiz(db, quiz_id=quiz.id, questions=questions)
    return generated_quiz_to_dict(db, quiz.id)


def add_questions_to_quiz(
    db: Session, *, quiz_id: int, questions: list[GeneratedQuestionInput]
) -> dict[str, Any]:
    quiz = db.scalar(
        select(Quiz)
        .where(Quiz.id == quiz_id)
        .options(selectinload(Quiz.primary_subject), selectinload(Quiz.question_links))
    )
    if quiz is None:
        raise ValueError(f"Unknown quiz id {quiz_id}")
    subject = quiz.primary_subject
    if subject is None:
        raise ValueError("Generated questions require a quiz with a primary subject.")

    prepared_questions = _questions_with_default_difficulty(questions, quiz.difficulty)
    question_bank_service.validate_question_batch(db, prepared_questions)
    starting_order = db.scalar(
        select(func.coalesce(func.max(QuizQuestion.order_index), 0)).where(
            QuizQuestion.quiz_id == quiz.id
        )
    )
    created = create_generated_questions(
        db,
        QuestionBankCreateInput(
            subject_name=subject.name,
            questions=prepared_questions,
            batch_metadata=BatchMetadata(generator_type="mcp_agent", notes=f"quiz_id={quiz.id}"),
        ),
    )
    inserted = db.scalars(
        select(Question).where(Question.id.in_(created["question_ids"])).order_by(Question.id)
    ).all()
    for offset, question in enumerate(inserted, start=1):
        quiz.question_links.append(
            QuizQuestion(
                question=question,
                order_index=(starting_order or 0) + offset,
                selection_reason="generated quiz compatibility path",
            )
        )
    db.commit()
    return {
        "quiz_id": quiz.id,
        "questions_added": len(inserted),
        "question_ids": [question.id for question in inserted],
    }


def generated_quiz_to_dict(db: Session, quiz_id: int) -> dict[str, Any]:
    quiz = db.scalar(
        select(Quiz)
        .where(Quiz.id == quiz_id)
        .options(
            selectinload(Quiz.primary_subject),
            selectinload(Quiz.questions).selectinload(Question.choices),
            selectinload(Quiz.questions).selectinload(Question.concepts),
        )
    )
    if quiz is None:
        raise ValueError(f"Unknown quiz id {quiz_id}")
    return {
        "id": quiz.id,
        "title": quiz.title,
        "description": quiz.description,
        "subject": _quiz_subject_name(quiz),
        "difficulty": quiz.difficulty.value,
        "quiz_type": quiz.quiz_type.value,
        "status": quiz.status.value,
        "questions": [_generated_question_to_dict(question) for question in quiz.questions],
    }


def _quiz_subject_name(quiz: Quiz) -> str | None:
    return quiz.primary_subject.name if quiz.primary_subject else None


def _generated_question_to_dict(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "stem": question.stem,
        "difficulty": question.difficulty.value,
        "source_reference": question.source_reference,
        "source_document_id": question.source_document_id,
        "source_excerpt": question.source_excerpt,
        "generation_batch_id": question.generation_batch_id,
        "health_state": question.health_state.value,
        "concepts": [concept.name for concept in question.concepts],
    }


def create_generated_questions(
    db: Session,
    data: QuestionBankCreateInput,
) -> dict[str, Any]:
    return question_bank_service.create_generated_questions(db, data)


def list_question_bank(
    db: Session,
    *,
    subject_id: int | None = None,
) -> list[dict[str, Any]]:
    return question_bank_service.list_question_bank(db, subject_id=subject_id)


def _questions_with_default_difficulty(
    questions: list[GeneratedQuestionInput],
    difficulty: Difficulty,
) -> list[GeneratedQuestionInput]:
    return [
        question
        if question.difficulty is not None
        else question.model_copy(update={"difficulty": difficulty})
        for question in questions
    ]
