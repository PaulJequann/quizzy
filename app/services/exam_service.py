from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.auth.models import User
from app.database import get_session_factory
from app.models import ExamAssignment, ExamSubmission, Question, Quiz, QuizStatus, utc_now
from app.services.exam_errors import ExamError
from app.services.exam_paper import (
    Paper,
    paper_from_quiz,
    paper_json,
    parse_paper,
)
from app.services.exam_responses import normalize_responses
from app.study_access import StudyAccess, StudyAccessDenied, resolve_study_access
from app.workspace_database import WorkspaceSession, workspace_session

MAX_REVIEW_NOTE_LENGTH = 4000


@dataclass(frozen=True)
class ExamChoice:
    key: int
    text: str


@dataclass(frozen=True)
class ExamQuestion:
    key: int
    stem: str
    choices: tuple[ExamChoice, ...]


@dataclass(frozen=True)
class ExamTakeView:
    id: int
    title: str
    questions: tuple[ExamQuestion, ...]


@dataclass(frozen=True)
class ExamPendingView:
    id: int
    title: str
    submitted_at: datetime


@dataclass(frozen=True)
class ExamAwaitingView:
    id: int
    title: str
    assigned_at: datetime


@dataclass(frozen=True)
class ExamAnswerFeedback:
    question: str
    selected_choice: str | None
    correct_choice: str
    is_correct: bool
    explanation: str


@dataclass(frozen=True)
class ExamResultView:
    id: int
    title: str
    score: float
    correct_count: int
    total_questions: int
    feedback: tuple[ExamAnswerFeedback, ...]
    review_note: str


@dataclass(frozen=True)
class ExamReviewView:
    id: int
    title: str
    submitted_at: datetime
    released: bool
    result: ExamResultView


@dataclass(frozen=True)
class ExamSummary:
    id: int
    title: str
    state: str
    assigned_at: datetime
    submitted_at: datetime | None


@dataclass(frozen=True)
class ExamReceipt:
    exam_id: int
    submitted_at: datetime


@dataclass(frozen=True)
class AssignmentDraft:
    reviewer_id: int
    source_quiz_id: int
    request_key: UUID
    paper: Paper
    title: str


type ExamPage = ExamTakeView | ExamPendingView | ExamAwaitingView | ExamResultView | ExamReviewView


class ExamNotFound(ExamError):
    pass


class ExamAlreadySubmitted(ExamError):
    pass


def assign_exam(
    *, access: StudyAccess, learner_id: int, source_quiz_id: int, request_key: UUID
) -> ExamSummary:
    if not access.can_assign_exams:
        raise StudyAccessDenied("Only a reviewer in their own workspace can assign exams")
    learner_access = _learner_access(access, learner_id)
    with workspace_session(learner_access) as learner_db:
        existing = _find_assignment(learner_db, access.actor_id, request_key)
        if existing is not None:
            return _summary(learner_db, existing)
    paper, title = _source_paper(access, source_quiz_id)
    with workspace_session(learner_access) as learner_db:
        draft = AssignmentDraft(access.actor_id, source_quiz_id, request_key, paper, title)
        return _create_assignment(learner_db, draft)


def _learner_access(access: StudyAccess, learner_id: int) -> StudyAccess:
    if learner_id == access.actor_id:
        raise ExamError("Choose a learner account")
    with get_session_factory()() as accounts:
        learner = accounts.scalar(select(User).filter_by(id=learner_id, is_active=True))
        if learner is None or learner.is_superuser:
            raise ExamNotFound("Learner account not found")
        return resolve_study_access(accounts, learner)


def _find_assignment(db: WorkspaceSession, reviewer_id: int, request_key: UUID):
    return db.scalar(
        select(ExamAssignment).where(
            ExamAssignment.assigned_by_id == reviewer_id,
            ExamAssignment.request_key == request_key,
        )
    )


def _source_paper(access: StudyAccess, source_quiz_id: int) -> tuple[Paper, str]:
    with workspace_session(access) as reviewer_db:
        quiz = reviewer_db.scalar(
            select(Quiz)
            .options(selectinload(Quiz.questions).selectinload(Question.choices))
            .where(Quiz.id == source_quiz_id, Quiz.status == QuizStatus.PUBLISHED)
        )
        if quiz is None:
            raise ExamNotFound("Published quiz not found")
        return paper_from_quiz(quiz), quiz.title


def _create_assignment(
    db: WorkspaceSession,
    draft: AssignmentDraft,
) -> ExamSummary:
    assignment = _assignment_record(draft)
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _find_assignment(db, draft.reviewer_id, draft.request_key)
        if existing is None:
            raise
        return _summary(db, existing)
    db.refresh(assignment)
    return ExamSummary(
        assignment.id, assignment.title_snapshot, "assigned", assignment.assigned_at, None
    )


def _assignment_record(draft: AssignmentDraft) -> ExamAssignment:
    return ExamAssignment(
        assigned_by_id=draft.reviewer_id,
        source_quiz_id=draft.source_quiz_id,
        title_snapshot=draft.title,
        paper_version=draft.paper.version,
        paper_json=paper_json(draft.paper),
        request_key=draft.request_key,
    )


def list_exams(db: WorkspaceSession, access: StudyAccess) -> list[ExamSummary]:
    _require_exam_view_access(access)
    assignments = list(
        db.scalars(select(ExamAssignment).order_by(ExamAssignment.assigned_at.desc()))
    )
    return [_summary(db, assignment) for assignment in assignments]


def exam_page(db: WorkspaceSession, access: StudyAccess, exam_id: int) -> ExamPage:
    _require_exam_view_access(access)
    assignment = db.scalar(select(ExamAssignment).where(ExamAssignment.id == exam_id))
    if assignment is None:
        raise ExamNotFound("Exam not found")
    submission = db.scalar(
        select(ExamSubmission).where(ExamSubmission.assignment_id == assignment.id)
    )
    if access.is_preview:
        if submission is None:
            return ExamAwaitingView(
                assignment.id, assignment.title_snapshot, assignment.assigned_at
            )
        result = _result(assignment, submission)
        return ExamReviewView(
            assignment.id,
            assignment.title_snapshot,
            submission.submitted_at,
            submission.released_at is not None,
            result,
        )
    if submission is None:
        return _take_view(assignment)
    if submission.released_at is None:
        return ExamPendingView(assignment.id, assignment.title_snapshot, submission.submitted_at)
    return _result(assignment, submission)


def submit_exam(
    db: WorkspaceSession,
    access: StudyAccess,
    exam_id: int,
    responses: Mapping[int, int | None],
) -> ExamReceipt:
    if not access.can_record_progress:
        raise StudyAccessDenied("Preview cannot change learner progress")
    assignment = _locked_assignment(db, exam_id)
    paper = parse_paper(assignment.paper_version, assignment.paper_json)
    normalized = normalize_responses(paper, responses)
    existing = _find_submission(db, assignment.id)
    if existing is not None:
        return _retry_receipt(assignment, existing, normalized)
    return _record_submission(db, assignment, normalized)


def _locked_assignment(db: WorkspaceSession, exam_id: int) -> ExamAssignment:
    assignment = db.scalar(
        select(ExamAssignment).where(ExamAssignment.id == exam_id).with_for_update()
    )
    if assignment is None:
        raise ExamNotFound("Exam not found")
    return assignment


def _find_submission(db: WorkspaceSession, assignment_id: int) -> ExamSubmission | None:
    return db.scalar(select(ExamSubmission).where(ExamSubmission.assignment_id == assignment_id))


def _retry_receipt(
    assignment: ExamAssignment,
    existing: ExamSubmission,
    normalized: dict[str, int | None],
) -> ExamReceipt:
    if existing.responses_json != normalized:
        raise ExamAlreadySubmitted("This exam has already been submitted")
    return ExamReceipt(assignment.id, existing.submitted_at)


def _record_submission(
    db: WorkspaceSession, assignment: ExamAssignment, normalized: dict[str, int | None]
) -> ExamReceipt:
    submission = ExamSubmission(assignment_id=assignment.id, responses_json=normalized)
    db.add(submission)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ExamAlreadySubmitted("This exam has already been submitted") from exc
    return ExamReceipt(assignment.id, submission.submitted_at)


def release_exam(
    db: WorkspaceSession, access: StudyAccess, exam_id: int, *, note: str
) -> ExamResultView:
    if not access.can_review_exams:
        raise StudyAccessDenied("Only a reviewer can release exam results")
    if len(note) > MAX_REVIEW_NOTE_LENGTH:
        raise ExamError(f"Review note must be at most {MAX_REVIEW_NOTE_LENGTH} characters")
    assignment = _get_assignment(db, exam_id)
    submission = _locked_submission(db, assignment.id)
    _release_submission(db, submission, access.actor_id, note)
    return _result(assignment, submission)


def _get_assignment(db: WorkspaceSession, exam_id: int) -> ExamAssignment:
    assignment = db.scalar(select(ExamAssignment).where(ExamAssignment.id == exam_id))
    if assignment is None:
        raise ExamNotFound("Exam not found")
    return assignment


def _locked_submission(db: WorkspaceSession, assignment_id: int) -> ExamSubmission:
    submission = db.scalar(
        select(ExamSubmission)
        .where(ExamSubmission.assignment_id == assignment_id)
        .with_for_update()
    )
    if submission is None:
        raise ExamError("The learner has not submitted this exam")
    return submission


def _release_submission(
    db: WorkspaceSession, submission: ExamSubmission, reviewer_id: int, note: str
) -> None:
    if submission.released_at is None:
        submission.reviewed_by_id = reviewer_id
        submission.review_note = note.strip()
        submission.released_at = utc_now()
        db.commit()


def _require_exam_view_access(access: StudyAccess) -> None:
    if access.is_preview and not access.can_review_exams:
        raise StudyAccessDenied("Only a reviewer can view assigned exam work")
    if not access.is_preview and not access.can_record_progress:
        raise StudyAccessDenied("Learner workspace access required")


def _summary(db: WorkspaceSession, assignment: ExamAssignment) -> ExamSummary:
    submission = db.scalar(
        select(ExamSubmission).where(ExamSubmission.assignment_id == assignment.id)
    )
    if submission is None:
        state = "assigned"
    elif submission.released_at is None:
        state = "pending"
    else:
        state = "released"
    return ExamSummary(
        assignment.id,
        assignment.title_snapshot,
        state,
        assignment.assigned_at,
        submission.submitted_at if submission else None,
    )


def _take_view(assignment: ExamAssignment) -> ExamTakeView:
    paper = parse_paper(assignment.paper_version, assignment.paper_json)
    return ExamTakeView(
        assignment.id,
        assignment.title_snapshot,
        tuple(
            ExamQuestion(
                question.key,
                question.stem,
                tuple(ExamChoice(choice.key, choice.text) for choice in question.choices),
            )
            for question in paper.questions
        ),
    )


def _result(assignment: ExamAssignment, submission: ExamSubmission) -> ExamResultView:
    paper = parse_paper(assignment.paper_version, assignment.paper_json)
    responses = _decode_responses(paper, submission.responses_json)
    feedback: list[ExamAnswerFeedback] = []
    correct_count = 0
    for question in paper.questions:
        selected_key = responses[question.key]
        selected_choice = next(
            (choice for choice in question.choices if choice.key == selected_key), None
        )
        correct_choice = next(
            choice for choice in question.choices if choice.key == question.correct_choice_key
        )
        is_correct = selected_key == question.correct_choice_key
        correct_count += is_correct
        feedback.append(
            ExamAnswerFeedback(
                question.stem,
                selected_choice.text if selected_choice else None,
                correct_choice.text,
                is_correct,
                question.explanation,
            )
        )
    total = len(paper.questions)
    return ExamResultView(
        assignment.id,
        assignment.title_snapshot,
        round(correct_count / total * 100, 2),
        correct_count,
        total,
        tuple(feedback),
        submission.review_note or "",
    )


def _decode_responses(paper: Paper, raw: object) -> dict[int, int | None]:
    try:
        decoded = _response_map(raw)
        normalized = normalize_responses(paper, decoded)
        return {int(key): value for key, value in normalized.items()}
    except (TypeError, ValueError, ExamError) as exc:
        raise ExamError("This exam response is invalid") from exc


def _response_map(raw: object) -> dict[int, int | None]:
    if not isinstance(raw, dict):
        raise ValueError
    decoded: dict[int, int | None] = {}
    for key, value in raw.items():
        decoded[_decode_response_key(key)] = _decode_response_value(value)
    return decoded


def _decode_response_key(raw: object) -> int:
    if not isinstance(raw, str):
        raise ValueError
    return int(raw)


def _decode_response_value(raw: object) -> int | None:
    if raw is not None and type(raw) is not int:
        raise ValueError
    return raw
