import re

from sqlalchemy import Select, func, select
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
    Subject,
)
from app.schemas import ChoiceCreate, QuestionCreate, QuizCreate


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


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    return slug or "untitled"


def get_or_create_subject(db: Session, name: str) -> Subject:
    subject_name = name.strip() or "General"
    slug = slugify(subject_name)
    existing = db.scalar(select(Subject).where(Subject.slug == slug))
    if existing is not None:
        return existing

    subject = Subject(name=subject_name, slug=slug)
    db.add(subject)
    return subject


def get_or_create_concepts(db: Session, names: list[str], subject: Subject) -> list[Concept]:
    concepts: list[Concept] = []
    seen: set[str] = set()
    for raw_name in names:
        name = raw_name.strip()
        slug = slugify(name)
        if not name or slug in seen:
            continue
        seen.add(slug)

        existing = db.scalar(
            select(Concept).where(Concept.subject == subject, Concept.slug == slug)
        )
        if existing is not None:
            concepts.append(existing)
            continue

        concept = Concept(name=name, slug=slug, subject=subject)
        db.add(concept)
        db.flush()
        concepts.append(concept)
    return concepts


def create_question(db: Session, quiz: Quiz, data: QuestionCreate) -> Question:
    order_index = db.scalar(
        select(func.coalesce(func.max(QuizQuestion.order_index), 0) + 1).where(
            QuizQuestion.quiz_id == quiz.id
        )
    )
    subject = quiz.primary_subject or get_or_create_subject(db, "General")
    concepts = get_or_create_concepts(db, data.concept_names, subject)
    question = Question(
        subject=subject,
        stem=data.stem.strip(),
        explanation=(data.explanation or "").strip() or None,
        difficulty=quiz.difficulty,
        status=QuestionStatus.ACTIVE,
        source_reference=(data.source_reference or "").strip() or None,
    )
    db.add(question)
    for index, choice_data in enumerate(data.choices, start=1):
        question.choices.append(
            Choice(
                text=choice_data.text.strip(),
                is_correct=choice_data.is_correct,
                order_index=index,
            )
        )
    question.concept_links = [
        QuestionConcept(concept=concept, is_primary=index == 0)
        for index, concept in enumerate(concepts)
    ]
    quiz.question_links.append(QuizQuestion(question=question, order_index=order_index or 1))
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
    stem: str,
    choices: list[str],
    correct_choice: int,
    explanation: str | None,
    source_reference: str | None,
    concepts: str | None = None,
) -> QuestionCreate:
    if len(choices) != 4:
        raise ValueError("Exactly four choices are required.")
    if correct_choice not in range(4):
        raise ValueError("Correct choice must be one of the four answer options.")
    if any(not choice.strip() for choice in choices):
        raise ValueError("Every answer choice needs text.")

    return QuestionCreate(
        stem=stem,
        explanation=explanation,
        source_reference=source_reference,
        concept_names=parse_concept_names(concepts),
        choices=[
            ChoiceCreate(text=choice, is_correct=index == correct_choice)
            for index, choice in enumerate(choices)
        ],
    )
