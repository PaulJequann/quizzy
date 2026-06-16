from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Difficulty,
    Question,
    QuestionConcept,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.services import concept_planning_service, concept_service, question_bank_service
from app.services.question_bank_service import BatchMetadata, QuestionBankCreateInput
from app.services.quiz_service import get_or_create_subject
from app.services.settings_service import get_default_difficulty
from app.services.text_service import slugify


class GeneratedChoiceInput(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False


class GeneratedConceptProposal(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    parent_name: str | None = Field(default=None, max_length=160)
    role: ConceptAssignmentRole = ConceptAssignmentRole.PRIMARY
    rationale: str = ""


class GeneratedQuestionInput(BaseModel):
    stem: str = Field(min_length=1)
    choices: list[GeneratedChoiceInput] = Field(min_length=4, max_length=4)
    explanation: str | None = None
    difficulty: Difficulty | None = Field(
        default=None,
        description=(
            "Question difficulty. Valid values are beginner, intermediate, advanced, or mixed; "
            "omit to use the app default."
        ),
    )
    concept_proposals: list[GeneratedConceptProposal] = Field(default_factory=list)
    source_document_id: int | None = Field(
        default=None,
        description="Ingested source document id that grounds this generated question.",
    )
    source_snapshot_id: int | None = Field(
        default=None,
        description=(
            "Optional source document snapshot id. Defaults to the source document's current "
            "snapshot when source_document_id is provided."
        ),
    )
    source_reference: str | None = Field(
        default=None,
        description=(
            "Explicit source reference when source_document_id is not available. Use this exact "
            "field name, not source_ref."
        ),
    )
    source_excerpt: str | None = None


@dataclass(frozen=True)
class GeneratedQuizCreateInput:
    title: str
    subject_name: str
    description: str = ""
    difficulty: Difficulty | None = None
    status: QuizStatus = QuizStatus.DRAFT


@dataclass(frozen=True)
class GeneratedQuestionWriteOptions:
    approve_new_concepts: bool = False
    save_unresolved_as_draft: bool = False
    draft_review_type: str = "unresolved_assignment"
    draft_review_reason: str = ""
    draft_suggested_action: str = "approve_or_reject_concept"
    draft_review_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class AddQuestionsToQuizInput:
    quiz_id: int
    questions: list[GeneratedQuestionInput]
    options: GeneratedQuestionWriteOptions = GeneratedQuestionWriteOptions()


DEFAULT_WRITE_OPTIONS = GeneratedQuestionWriteOptions()


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


def list_concepts(
    db: Session,
    *,
    subject_name: str | None = None,
    statuses: list[ConceptStatus] | None = None,
) -> list[dict[str, Any]]:
    requested_statuses = statuses or [ConceptStatus.ACTIVE]
    statement = (
        select(Concept)
        .where(Concept.status.in_(requested_statuses))
        .options(selectinload(Concept.subject))
        .order_by(Concept.status, Concept.name)
    )
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
            "normalized_name": concept.normalized_name,
            "status": concept.status.value,
            "parent_concept_id": concept.parent_concept_id,
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
    *,
    options: GeneratedQuestionWriteOptions = DEFAULT_WRITE_OPTIONS,
) -> dict[str, Any]:
    question_bank_service.validate_question_batch(db, questions)
    quiz = create_generated_quiz(db, quiz_input)
    add_questions_to_quiz(
        db,
        AddQuestionsToQuizInput(
            quiz_id=quiz.id,
            questions=questions,
            options=options,
        ),
    )
    return generated_quiz_to_dict(db, quiz.id)


def add_questions_to_quiz(
    db: Session,
    data: AddQuestionsToQuizInput,
) -> dict[str, Any]:
    quiz = db.scalar(
        select(Quiz)
        .where(Quiz.id == data.quiz_id)
        .options(selectinload(Quiz.primary_subject), selectinload(Quiz.question_links))
    )
    if quiz is None:
        raise ValueError(f"Unknown quiz id {data.quiz_id}")
    subject = quiz.primary_subject
    if subject is None:
        raise ValueError("Generated questions require a quiz with a primary subject.")

    prepared_questions = _questions_with_default_difficulty(data.questions, quiz.difficulty)
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
            batch_metadata=BatchMetadata(
                generator_type="mcp_agent",
                notes=f"quiz_id={quiz.id}",
            ),
            approve_new_concepts=data.options.approve_new_concepts,
            save_unresolved_as_draft=data.options.save_unresolved_as_draft,
            draft_review_type=data.options.draft_review_type,
            draft_review_reason=data.options.draft_review_reason,
            draft_suggested_action=data.options.draft_suggested_action,
            draft_review_evidence=data.options.draft_review_evidence,
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
            selectinload(Quiz.questions)
            .selectinload(Question.concept_links)
            .selectinload(QuestionConcept.concept),
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
        "status": question.status.value,
        "source_reference": question.source_reference,
        "source_document_id": question.source_document_id,
        "source_snapshot_id": question.source_snapshot_id,
        "source_excerpt": question.source_excerpt,
        "generation_batch_id": question.generation_batch_id,
        "health_state": question.health_state.value,
        "concepts": [
            {
                "id": link.concept.id,
                "name": link.concept.name,
                "role": link.role.value,
                "status": link.concept.status.value,
                "parent_concept_id": link.concept.parent_concept_id,
            }
            for link in sorted(
                question.concept_links,
                key=lambda link: (link.role.value, link.concept.name.casefold()),
            )
        ],
    }


def create_generated_questions(
    db: Session,
    data: QuestionBankCreateInput,
) -> dict[str, Any]:
    return question_bank_service.create_generated_questions(db, data)


def prepare_concept_plan(
    db: Session,
    *,
    subject_name: str,
    proposals: list[GeneratedConceptProposal],
) -> dict[str, Any]:
    return concept_planning_service.prepare_concept_plan(
        db,
        subject_name=subject_name,
        proposals=[_generated_proposal_to_domain(proposal) for proposal in proposals],
    )


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


def _generated_proposal_to_domain(
    proposal: GeneratedConceptProposal,
) -> concept_service.ConceptProposal:
    return concept_service.ConceptProposal(
        name=proposal.name,
        parent_name=proposal.parent_name,
        role=proposal.role,
        rationale=proposal.rationale,
    )
