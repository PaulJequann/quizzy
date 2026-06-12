from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Choice, Concept, Difficulty, Question, Quiz, QuizStatus
from app.schemas import ChoiceCreate, QuestionCreate, QuizCreate


def quiz_query() -> Select[tuple[Quiz]]:
    return select(Quiz).options(
        selectinload(Quiz.questions).selectinload(Question.choices),
        selectinload(Quiz.questions).selectinload(Question.concepts),
    )


def list_quizzes(db: Session) -> list[Quiz]:
    return list(db.scalars(quiz_query().order_by(Quiz.created_at.desc())))


def get_quiz(db: Session, quiz_id: int) -> Quiz | None:
    return db.scalar(quiz_query().where(Quiz.id == quiz_id))


def create_quiz(db: Session, data: QuizCreate) -> Quiz:
    quiz = Quiz(
        title=data.title.strip(),
        description=data.description.strip(),
        topic=data.topic.strip(),
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


def get_or_create_concepts(db: Session, names: list[str], topic: str) -> list[Concept]:
    concepts: list[Concept] = []
    seen: set[str] = set()
    for raw_name in names:
        name = raw_name.strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)

        existing = db.scalar(select(Concept).where(func.lower(Concept.name) == name.lower()))
        if existing is not None:
            concepts.append(existing)
            continue

        concept = Concept(name=name, topic=topic.strip())
        db.add(concept)
        concepts.append(concept)
    return concepts


def create_question(db: Session, quiz: Quiz, data: QuestionCreate) -> Question:
    order_index = db.scalar(
        select(func.coalesce(func.max(Question.order_index), 0) + 1).where(
            Question.quiz_id == quiz.id
        )
    )
    question = Question(
        quiz_id=quiz.id,
        stem=data.stem.strip(),
        explanation=(data.explanation or "").strip() or None,
        source_reference=(data.source_reference or "").strip() or None,
        order_index=order_index or 1,
    )
    for index, choice_data in enumerate(data.choices, start=1):
        question.choices.append(
            Choice(
                text=choice_data.text.strip(),
                is_correct=choice_data.is_correct,
                order_index=index,
            )
        )
    question.concepts = get_or_create_concepts(db, data.concept_names, quiz.topic)
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


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
