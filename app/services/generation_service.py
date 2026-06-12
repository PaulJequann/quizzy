from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Choice,
    Concept,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionStatus,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    SourceDocument,
    Subject,
)
from app.services.quiz_service import get_or_create_concepts, get_or_create_subject, slugify
from app.services.settings_service import get_default_difficulty

MAX_CONCEPTS_PER_GENERATED_QUESTION = 2


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


@dataclass(frozen=True)
class QuestionBuildContext:
    quiz: Quiz
    subject: Subject
    order_index: int


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
    _validate_question_batch(db, questions)
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

    _validate_question_batch(db, questions)
    starting_order = db.scalar(
        select(func.coalesce(func.max(QuizQuestion.order_index), 0)).where(
            QuizQuestion.quiz_id == quiz.id
        )
    )
    inserted: list[Question] = []
    for offset, question_input in enumerate(questions, start=1):
        question = _build_question(
            db,
            context=QuestionBuildContext(
                quiz=quiz,
                subject=subject,
                order_index=(starting_order or 0) + offset,
            ),
            question_input=question_input,
        )
        inserted.append(question)
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
        "source_excerpt": question.source_excerpt,
        "concepts": [concept.name for concept in question.concepts],
    }


def _build_question(
    db: Session,
    *,
    context: QuestionBuildContext,
    question_input: GeneratedQuestionInput,
) -> Question:
    _validate_question(question_input)
    source_reference = _source_reference(db, question_input)
    concepts = get_or_create_concepts(db, question_input.concept_names, context.subject)
    question = Question(
        subject=context.subject,
        stem=question_input.stem.strip(),
        explanation=(question_input.explanation or "").strip() or None,
        difficulty=question_input.difficulty or context.quiz.difficulty,
        status=QuestionStatus.ACTIVE,
        source_reference=source_reference,
        source_excerpt=(question_input.source_excerpt or "").strip() or None,
    )
    db.add(question)
    for index, choice_input in enumerate(question_input.choices, start=1):
        question.choices.append(
            Choice(
                text=choice_input.text.strip(),
                is_correct=choice_input.is_correct,
                order_index=index,
            )
        )
    question.concept_links = [
        QuestionConcept(concept=concept, is_primary=index == 0)
        for index, concept in enumerate(concepts)
    ]
    context.quiz.question_links.append(
        QuizQuestion(question=question, order_index=context.order_index)
    )
    return question


def _validate_question(question_input: GeneratedQuestionInput) -> None:
    _validate_choices(question_input)
    _validate_source(question_input)
    _validate_concepts(question_input)


def _validate_choices(question_input: GeneratedQuestionInput) -> None:
    if len(question_input.choices) != 4:
        raise ValueError("Generated questions must have exactly four choices.")
    correct_count = sum(choice.is_correct for choice in question_input.choices)
    if correct_count != 1:
        raise ValueError("Generated questions must have exactly one correct choice.")


def _validate_source(question_input: GeneratedQuestionInput) -> None:
    if not _has_source(question_input):
        raise ValueError("Generated questions require source_document_id or source_reference.")


def _validate_concepts(question_input: GeneratedQuestionInput) -> None:
    concept_names = _normalized_concept_names(question_input)
    if not concept_names:
        raise ValueError("Generated questions require at least one concept.")
    if len(concept_names) > MAX_CONCEPTS_PER_GENERATED_QUESTION:
        raise ValueError("Generated questions can tag at most two concepts.")


def _has_source(question_input: GeneratedQuestionInput) -> bool:
    return question_input.source_document_id is not None or bool(
        (question_input.source_reference or "").strip()
    )


def _normalized_concept_names(question_input: GeneratedQuestionInput) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in question_input.concept_names:
        name = raw_name.strip()
        slug = slugify(name)
        if name and slug not in seen:
            names.append(name)
            seen.add(slug)
    return names


def _validate_question_batch(db: Session, questions: list[GeneratedQuestionInput]) -> None:
    for question_input in questions:
        _validate_question(question_input)
        _source_reference(db, question_input)


def _source_reference(db: Session, question_input: GeneratedQuestionInput) -> str:
    explicit_reference = (question_input.source_reference or "").strip()
    if explicit_reference:
        return explicit_reference
    if question_input.source_document_id is None:
        raise ValueError("Generated questions require source_document_id or source_reference.")
    document = db.scalar(
        select(SourceDocument).where(SourceDocument.id == question_input.source_document_id)
    )
    if document is None:
        raise ValueError(f"Unknown source document id {question_input.source_document_id}")
    return document.path
