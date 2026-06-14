from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Choice,
    Concept,
    ConceptAssignmentRole,
    Difficulty,
    Question,
    QuestionStatus,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.schemas import ChoiceCreate, ConceptProposalInput, QuestionCreate, QuizCreate
from app.services import concept_service
from app.services.text_service import slugify


@dataclass(frozen=True)
class QuestionFormInput:
    stem: str
    choices: list[str]
    correct_choice: int
    explanation: str | None
    source_reference: str | None
    concepts: str | None = None


def quiz_query() -> Select[tuple[Quiz]]:
    return select(Quiz).options(
        selectinload(Quiz.primary_subject),
        selectinload(Quiz.questions).selectinload(Question.choices),
        selectinload(Quiz.questions).selectinload(Question.subject),
        selectinload(Quiz.questions).selectinload(Question.concepts).selectinload(Concept.subject),
        selectinload(Quiz.questions).selectinload(Question.quiz_links),
    )


def list_quizzes(db: Session) -> list[Quiz]:
    return list(db.scalars(quiz_query().order_by(Quiz.created_at.desc())))


def get_quiz(db: Session, quiz_id: int) -> Quiz | None:
    return db.scalar(quiz_query().where(Quiz.id == quiz_id))


def create_quiz(db: Session, data: QuizCreate) -> Quiz:
    primary_subject = get_or_create_subject(db, data.topic) if data.topic.strip() else None
    quiz = Quiz(
        title=data.title.strip(),
        description=data.description.strip(),
        primary_subject=primary_subject,
        difficulty=data.difficulty,
        status=data.status,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


def update_quiz_status(db: Session, quiz: Quiz, status: QuizStatus) -> Quiz:
    quiz.status = status
    db.commit()
    db.refresh(quiz)
    return quiz


def parse_concept_names(raw_concepts: str | None) -> list[str]:
    if not raw_concepts:
        return []

    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_concepts.split(","):
        name = raw_name.strip()
        key = name.casefold()
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def get_or_create_subject(db: Session, name: str) -> Subject:
    subject_name = name.strip() or "General"
    slug = slugify(subject_name)
    existing = db.scalar(select(Subject).where(Subject.slug == slug))
    if existing is not None:
        return existing

    subject = Subject(name=subject_name, slug=slug)
    db.add(subject)
    return subject


def create_question(db: Session, quiz: Quiz, data: QuestionCreate) -> Question:
    subject = quiz.primary_subject or get_or_create_subject(db, "General")
    assignments = _manual_assignments(db, subject, data)
    question = _question_from_create_input(subject, quiz, data)
    db.add(question)
    _attach_choices(question, data.choices)
    concept_service.apply_question_assignments(question, assignments)
    db.flush()
    _record_manual_assignments(db, subject, question, assignments)
    quiz.question_links.append(
        QuizQuestion(question=question, order_index=_next_quiz_question_order(db, quiz))
    )
    db.commit()
    db.refresh(question)
    return question


def create_review_quiz_from_concepts(db: Session, concept_ids: list[int]) -> Quiz:
    if not concept_ids:
        raise ValueError("At least one concept is required to create a review quiz.")

    concepts = {
        concept.id: concept
        for concept in db.scalars(
            select(Concept)
            .where(Concept.id.in_(concept_ids))
            .options(
                selectinload(Concept.questions).selectinload(Question.choices),
                selectinload(Concept.subject),
                selectinload(Concept.questions).selectinload(Question.concepts),
                selectinload(Concept.questions).selectinload(Question.quiz_links),
            )
        )
    }

    source_questions: list[Question] = []
    seen_question_ids: set[int] = set()
    for concept_id in concept_ids:
        concept = concepts.get(concept_id)
        if concept is None:
            continue

        ordered_questions = sorted(
            concept.questions,
            key=lambda question: (question.order_index, question.id),
        )
        for question in ordered_questions:
            if question.id in seen_question_ids:
                continue
            seen_question_ids.add(question.id)
            source_questions.append(question)

    if not source_questions:
        raise ValueError("No questions are tagged with the queued concepts.")

    quiz = Quiz(
        title="Weak Concept Review",
        description="A deterministic review quiz built from weak and improving concept tags.",
        difficulty=Difficulty.BEGINNER,
        quiz_type=QuizType.REVIEW,
        status=QuizStatus.PUBLISHED,
    )
    linked_subject_ids = {question.subject_id for question in source_questions}
    if len(linked_subject_ids) == 1:
        quiz.primary_subject = source_questions[0].subject
    for question_index, source_question in enumerate(source_questions, start=1):
        quiz.question_links.append(
            QuizQuestion(question=source_question, order_index=question_index)
        )

    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


def form_quiz_create(title: str, description: str, topic: str, difficulty: str) -> QuizCreate:
    return QuizCreate(
        title=title,
        description=description,
        topic=topic,
        difficulty=Difficulty(difficulty),
    )


def form_question_create(
    data: QuestionFormInput,
) -> QuestionCreate:
    if len(data.choices) != 4:
        raise ValueError("Exactly four choices are required.")
    if data.correct_choice not in range(4):
        raise ValueError("Correct choice must be one of the four answer options.")
    if any(not choice.strip() for choice in data.choices):
        raise ValueError("Every answer choice needs text.")

    return QuestionCreate(
        stem=data.stem,
        explanation=data.explanation,
        source_reference=data.source_reference,
        concept_proposals=_form_concept_proposals(data.concepts),
        choices=[
            ChoiceCreate(text=choice, is_correct=index == data.correct_choice)
            for index, choice in enumerate(data.choices)
        ],
    )


def _manual_assignments(
    db: Session,
    subject: Subject,
    data: QuestionCreate,
) -> list[concept_service.ResolvedConceptAssignment]:
    return concept_service.resolve_concept_assignments(
        db,
        concept_service.AssignmentResolutionRequest(
            subject=subject,
            proposals=[_schema_proposal_to_domain(proposal) for proposal in data.concept_proposals],
            allow_new_active=True,
            actor="user",
            source="web",
        ),
    )


def _question_from_create_input(subject: Subject, quiz: Quiz, data: QuestionCreate) -> Question:
    return Question(
        subject=subject,
        stem=data.stem.strip(),
        explanation=(data.explanation or "").strip() or None,
        difficulty=quiz.difficulty,
        status=QuestionStatus.ACTIVE,
        source_reference=(data.source_reference or "").strip() or None,
    )


def _attach_choices(question: Question, choices: list[ChoiceCreate]) -> None:
    question.choices = [
        Choice(
            text=choice_data.text.strip(),
            is_correct=choice_data.is_correct,
            order_index=index,
        )
        for index, choice_data in enumerate(choices, start=1)
    ]


def _record_manual_assignments(
    db: Session,
    subject: Subject,
    question: Question,
    assignments: list[concept_service.ResolvedConceptAssignment],
) -> None:
    concept_service.record_question_assignment_decision(
        db,
        concept_service.QuestionAssignmentDecision(
            subject=subject,
            question=question,
            assignments=assignments,
            actor="user",
            source="web",
        ),
    )


def _next_quiz_question_order(db: Session, quiz: Quiz) -> int:
    return (
        db.scalar(
            select(func.coalesce(func.max(QuizQuestion.order_index), 0) + 1).where(
                QuizQuestion.quiz_id == quiz.id
            )
        )
        or 1
    )


def _form_concept_proposals(concepts: str | None) -> list[ConceptProposalInput]:
    return [
        ConceptProposalInput(name=name, role=_form_concept_role(index))
        for index, name in enumerate(parse_concept_names(concepts))
    ]


def _form_concept_role(index: int) -> ConceptAssignmentRole:
    return ConceptAssignmentRole.PRIMARY if index == 0 else ConceptAssignmentRole.SECONDARY


def _schema_proposal_to_domain(
    proposal: ConceptProposalInput,
) -> concept_service.ConceptProposal:
    return concept_service.ConceptProposal(
        name=proposal.name,
        parent_name=proposal.parent_name,
        role=proposal.role,
        rationale=proposal.rationale,
    )
