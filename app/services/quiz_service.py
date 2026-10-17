from dataclasses import dataclass, field

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Concept,
    ConceptAssignmentRole,
    Difficulty,
    Question,
    QuestionStatus,
    QuestionType,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.schemas import (
    MAX_KEY_POINTS,
    MIN_KEY_POINTS,
    ChoiceCreate,
    ConceptProposalInput,
    MultipleChoiceQuestionCreate,
    OpenEndedQuestionCreate,
    QuestionCreate,
    QuizCreate,
)
from app.services import concept_service
from app.services.question_content import apply_content, is_open_ended
from app.services.text_service import slugify

SUBJECT_NAME_MAX_LENGTH = 160
QUIZ_TITLE_MAX_LENGTH = 200


@dataclass(frozen=True)
class QuestionFormInput:
    stem: str
    choices: list[str]
    correct_choice: int
    explanation: str | None
    source_reference: str | None
    concepts: str | None = None
    question_type: QuestionType = QuestionType.MULTIPLE_CHOICE
    model_answer: str = ""
    grading_notes: str | None = None
    key_points: list[str] = field(default_factory=list)


def quiz_query() -> Select[tuple[Quiz]]:
    return select(Quiz).options(
        selectinload(Quiz.primary_subject),
        selectinload(Quiz.questions).selectinload(Question.choices),
        selectinload(Quiz.questions).selectinload(Question.key_points),
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
        title=validate_quiz_title(data.title),
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
    subject_name = normalize_subject_name(name)
    slug = slugify(subject_name)
    existing = db.scalar(select(Subject).where(Subject.slug == slug))
    if existing is not None:
        return existing

    subject = Subject(name=subject_name, slug=slug)
    db.add(subject)
    return subject


def normalize_subject_name(name: str) -> str:
    subject_name = name.strip() or "General"
    if len(subject_name) > SUBJECT_NAME_MAX_LENGTH:
        raise ValueError(f"subject_name must be at most {SUBJECT_NAME_MAX_LENGTH} characters.")
    return subject_name


def validate_quiz_title(title: str) -> str:
    normalized = title.strip()
    if not normalized:
        raise ValueError("Quiz title is required.")
    if len(normalized) > QUIZ_TITLE_MAX_LENGTH:
        raise ValueError(f"Quiz title must be at most {QUIZ_TITLE_MAX_LENGTH} characters.")
    return normalized


def create_question(db: Session, quiz: Quiz, data: QuestionCreate) -> Question:
    exam = quiz.quiz_type == QuizType.EXAM
    if is_open_ended(data) and not exam:
        raise ValueError("Open-ended questions belong only in exam quizzes.")
    subject = quiz.primary_subject or get_or_create_subject(db, "General")
    assignments = _manual_assignments(db, subject, data)
    question = _question_from_create_input(subject, quiz, data)
    question.exam_only = exam
    apply_content(question, data)
    db.add(question)
    concept_service.apply_question_assignments(question, assignments)
    db.flush()
    _record_manual_assignments(db, subject, question, assignments)
    quiz.question_links.append(
        QuizQuestion(question=question, order_index=_next_quiz_question_order(db, quiz))
    )
    db.commit()
    db.refresh(question)
    return question


def update_question(db: Session, question: Question, data: QuestionCreate) -> Question:
    proposed = [proposal.name for proposal in data.concept_proposals]
    # Unchanged tags stay as they are, so a proposed concept awaiting review does not block edits.
    if proposed != _concept_names_primary_first(question):
        _reassign_concepts(db, question, data)
    question.stem = data.stem.strip()
    question.explanation = (data.explanation or "").strip() or None
    question.source_reference = (data.source_reference or "").strip() or None
    apply_content(question, data)
    db.commit()
    return question


def _reassign_concepts(db: Session, question: Question, data: QuestionCreate) -> None:
    assignments = _manual_assignments(db, question.subject, data)
    concept_service.apply_question_assignments(question, assignments)
    db.flush()
    _record_manual_assignments(db, question.subject, question, assignments)


def question_form_values(question: Question) -> QuestionFormInput:
    choices = [choice.text for choice in question.choices]
    correct = [choice.is_correct for choice in question.choices]
    return QuestionFormInput(
        stem=question.stem,
        choices=(choices + [""] * 4)[:4],
        correct_choice=correct.index(True) if True in correct else 0,
        explanation=question.explanation,
        source_reference=question.source_reference,
        concepts=", ".join(_concept_names_primary_first(question)),
        question_type=question.question_type,
        model_answer=question.model_answer or "",
        grading_notes=question.grading_notes,
        key_points=[point.text for point in question.key_points],
    )


def _concept_names_primary_first(question: Question) -> list[str]:
    links = sorted(
        question.concept_links, key=lambda link: link.role != ConceptAssignmentRole.PRIMARY
    )
    return [link.concept.name for link in links]


def create_review_quiz_from_concepts(db: Session, concept_ids: list[int]) -> Quiz:
    if not concept_ids:
        raise ValueError("At least one concept is required to create a review quiz.")

    concepts = _review_concepts_by_id(db, concept_ids)
    source_questions = _review_source_questions(concepts, concept_ids)
    if not source_questions:
        raise ValueError("No questions are tagged with the queued concepts.")

    quiz = _new_review_quiz(source_questions)
    _attach_review_questions(quiz, source_questions)
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


def _review_concepts_by_id(db: Session, concept_ids: list[int]) -> dict[int, Concept]:
    return {
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


def _review_source_questions(
    concepts: dict[int, Concept],
    concept_ids: list[int],
) -> list[Question]:
    source_questions: list[Question] = []
    seen_question_ids: set[int] = set()
    for concept_id in concept_ids:
        concept = concepts.get(concept_id)
        if concept is not None:
            _append_unseen_questions(source_questions, seen_question_ids, concept)
    return source_questions


def _append_unseen_questions(
    source_questions: list[Question],
    seen_question_ids: set[int],
    concept: Concept,
) -> None:
    for question in _ordered_concept_questions(concept):
        if question.id not in seen_question_ids:
            seen_question_ids.add(question.id)
            source_questions.append(question)


def _ordered_concept_questions(concept: Concept) -> list[Question]:
    return sorted(
        (question for question in concept.questions if not question.exam_only),
        key=lambda question: (question.order_index, question.id),
    )


def _new_review_quiz(source_questions: list[Question]) -> Quiz:
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
    return quiz


def _attach_review_questions(quiz: Quiz, source_questions: list[Question]) -> None:
    for question_index, source_question in enumerate(source_questions, start=1):
        quiz.question_links.append(
            QuizQuestion(question=source_question, order_index=question_index)
        )


def form_quiz_create(title: str, description: str, topic: str, difficulty: str) -> QuizCreate:
    return QuizCreate(
        title=title,
        description=description,
        topic=topic,
        difficulty=Difficulty(difficulty),
    )


def form_question_create(data: QuestionFormInput) -> QuestionCreate:
    if data.question_type == QuestionType.OPEN_ENDED:
        return _form_open_ended(data)
    return _form_multiple_choice(data)


def _form_multiple_choice(data: QuestionFormInput) -> MultipleChoiceQuestionCreate:
    _require_choice_form(data)
    return MultipleChoiceQuestionCreate(
        stem=data.stem,
        explanation=data.explanation,
        source_reference=data.source_reference,
        concept_proposals=_form_concept_proposals(data.concepts),
        choices=[
            ChoiceCreate(text=choice, is_correct=index == data.correct_choice)
            for index, choice in enumerate(data.choices)
        ],
    )


def _require_choice_form(data: QuestionFormInput) -> None:
    if len(data.choices) != 4:
        raise ValueError("Exactly four choices are required.")
    if data.correct_choice not in range(4):
        raise ValueError("Correct choice must be one of the four answer options.")
    if any(not choice.strip() for choice in data.choices):
        raise ValueError("Every answer choice needs text.")


def _form_open_ended(data: QuestionFormInput) -> OpenEndedQuestionCreate:
    key_points = [text.strip() for text in data.key_points if text.strip()]
    if not data.model_answer.strip():
        raise ValueError("Open-ended questions need a model answer.")
    if not MIN_KEY_POINTS <= len(key_points) <= MAX_KEY_POINTS:
        raise ValueError("Open-ended questions need two to four key points.")
    return OpenEndedQuestionCreate(
        question_type=QuestionType.OPEN_ENDED,
        stem=data.stem,
        explanation=data.explanation,
        source_reference=data.source_reference,
        concept_proposals=_form_concept_proposals(data.concepts),
        model_answer=data.model_answer,
        grading_notes=data.grading_notes,
        key_points=key_points,
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
