from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Concept, Question, Quiz, Subject
from app.services.grading_service import (
    ConceptMastery,
    ConceptQueueItem,
    build_concept_queue_items,
)


@dataclass(frozen=True)
class SubjectMasteryGroup:
    subject: Subject
    mastery_rows: list[ConceptMastery]
    queue_items: list[ConceptQueueItem]
    parent_concept_ids: set[int]
    total_attempts: int
    correct_attempts: int
    accuracy: float
    weak_count: int
    improving_count: int
    strong_count: int


@dataclass(frozen=True)
class SubjectSummary:
    subject: Subject
    concept_count: int
    question_count: int
    quiz_count: int
    weak_concept_count: int
    total_attempts: int
    accuracy: float


def build_subject_mastery_groups(
    mastery_rows: list[ConceptMastery],
) -> list[SubjectMasteryGroup]:
    rows_by_subject_id: dict[int, list[ConceptMastery]] = {}
    subjects_by_id: dict[int, Subject] = {}
    for row in mastery_rows:
        rows_by_subject_id.setdefault(row.concept.subject_id, []).append(row)
        subjects_by_id[row.concept.subject_id] = row.concept.subject

    groups: list[SubjectMasteryGroup] = []
    for subject_id, subject_rows in rows_by_subject_id.items():
        total_attempts = sum(row.total_attempts for row in subject_rows)
        correct_attempts = sum(row.correct_attempts for row in subject_rows)
        accuracy = round((correct_attempts / total_attempts) * 100, 2) if total_attempts else 0
        queue_items = build_concept_queue_items(subject_rows)
        groups.append(
            SubjectMasteryGroup(
                subject=subjects_by_id[subject_id],
                mastery_rows=_hierarchical_mastery_rows(subject_rows),
                queue_items=queue_items,
                parent_concept_ids=_parent_concept_ids(subject_rows),
                total_attempts=total_attempts,
                correct_attempts=correct_attempts,
                accuracy=accuracy,
                weak_count=sum(1 for row in subject_rows if row.status == "weak"),
                improving_count=sum(1 for row in subject_rows if row.status == "improving"),
                strong_count=sum(1 for row in subject_rows if row.status == "strong"),
            )
        )

    return sorted(groups, key=lambda group: group.subject.name.casefold())


def _parent_concept_ids(rows: list[ConceptMastery]) -> set[int]:
    parent_ids: set[int] = set()
    for row in rows:
        if row.concept.parent_concept_id is not None:
            parent_ids.add(row.concept.parent_concept_id)
    return parent_ids


def _hierarchical_mastery_rows(rows: list[ConceptMastery]) -> list[ConceptMastery]:
    rows_by_parent_id: dict[int | None, list[ConceptMastery]] = {}
    for row in rows:
        rows_by_parent_id.setdefault(row.concept.parent_concept_id, []).append(row)

    ordered_rows: list[ConceptMastery] = []

    def append_subtree(row: ConceptMastery) -> None:
        ordered_rows.append(row)
        children = sorted(
            rows_by_parent_id.get(row.concept.id, []),
            key=lambda child: child.concept.name.casefold(),
        )
        for child in children:
            append_subtree(child)

    roots = sorted(
        rows_by_parent_id.get(None, []),
        key=lambda row: row.concept.name.casefold(),
    )
    for root in roots:
        append_subtree(root)
    return ordered_rows


def get_subjects(db: Session) -> list[Subject]:
    subjects = list(
        db.scalars(
            select(Subject)
            .options(
                selectinload(Subject.concepts),
                selectinload(Subject.questions).selectinload(Question.quizzes),
                selectinload(Subject.primary_quizzes),
            )
            .order_by(Subject.name)
        )
    )
    return [
        subject
        for subject in subjects
        if subject.concepts or subject.questions or subject.primary_quizzes
    ]


def get_subject(db: Session, subject_id: int) -> Subject | None:
    return db.scalar(
        select(Subject)
        .where(Subject.id == subject_id)
        .options(
            selectinload(Subject.concepts)
            .selectinload(Concept.questions)
            .selectinload(Question.attempt_answers),
            selectinload(Subject.questions).selectinload(Question.concepts),
            selectinload(Subject.questions).selectinload(Question.quizzes),
            selectinload(Subject.primary_quizzes),
        )
    )


def quizzes_for_subject(subject: Subject) -> list[Quiz]:
    quizzes_by_id = {quiz.id: quiz for quiz in subject.primary_quizzes}
    for question in subject.questions:
        for quiz in question.quizzes:
            quizzes_by_id[quiz.id] = quiz
    return sorted(quizzes_by_id.values(), key=lambda quiz: quiz.title.casefold())


def build_subject_summaries(
    subjects: list[Subject],
    mastery_groups: list[SubjectMasteryGroup],
) -> list[SubjectSummary]:
    groups_by_subject_id = {group.subject.id: group for group in mastery_groups}
    summaries: list[SubjectSummary] = []
    for subject in subjects:
        group = groups_by_subject_id.get(subject.id)
        summaries.append(
            SubjectSummary(
                subject=subject,
                concept_count=len(subject.concepts),
                question_count=len(subject.questions),
                quiz_count=len(quizzes_for_subject(subject)),
                weak_concept_count=group.weak_count if group else 0,
                total_attempts=group.total_attempts if group else 0,
                accuracy=group.accuracy if group else 0,
            )
        )
    return summaries
