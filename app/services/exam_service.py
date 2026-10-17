from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models import (
    ExamAssignment,
    ExamSubmission,
    Question,
    Quiz,
    QuizStatus,
    QuizType,
    utc_now,
)
from app.services import exam_grades, question_health_service
from app.services.exam_attempt import exam_attempt
from app.services.exam_errors import ExamError
from app.services.exam_grades import ExamGrading
from app.services.exam_paper import (
    MultipleChoicePaperQuestion,
    OpenEndedPaperQuestion,
    Paper,
    PaperChoice,
    PaperQuestion,
    paper_from_quiz,
    paper_json,
    parse_paper,
)
from app.services.exam_responses import ResponseValue, normalize_responses, read_form_answers
from app.services.exam_results import (
    ExamRecord,
    ExamResultView,
    ReviewItem,
    exam_result,
    review_items,
)
from app.study_access import StudyAccess, StudyAccessDenied
from app.workspace_database import WorkspaceSession

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
    written: bool


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
class ExamReviewView:
    id: int
    title: str
    submitted_at: datetime
    released: bool
    result: ExamResultView
    items: tuple[ReviewItem, ...]


@dataclass(frozen=True)
class ExamSummary:
    id: int
    title: str
    state: str
    assigned_at: datetime
    submitted_at: datetime | None


@dataclass(frozen=True)
class PendingGrading:
    id: int
    title: str
    submitted_at: datetime
    written_count: int
    ungraded_count: int


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
    db: WorkspaceSession, access: StudyAccess, *, source_quiz_id: int, request_key: UUID
) -> ExamSummary:
    if not access.can_manage_exams:
        raise StudyAccessDenied("Only a reviewer previewing a learner can assign exams")
    existing = _find_assignment(db, access.actor_id, request_key)
    if existing is not None:
        return _summary(db, existing)
    paper, title = _source_paper(db, source_quiz_id)
    draft = AssignmentDraft(access.actor_id, source_quiz_id, request_key, paper, title)
    return _create_assignment(db, draft)


def _find_assignment(db: WorkspaceSession, reviewer_id: int, request_key: UUID):
    return db.scalar(
        select(ExamAssignment).where(
            ExamAssignment.assigned_by_id == reviewer_id,
            ExamAssignment.request_key == request_key,
        )
    )


def assignable_exam_quiz(db: WorkspaceSession, quiz_id: int) -> Quiz:
    quiz = db.scalar(
        select(Quiz)
        .options(
            selectinload(Quiz.questions).selectinload(Question.choices),
            selectinload(Quiz.questions).selectinload(Question.key_points),
        )
        .where(
            Quiz.id == quiz_id,
            Quiz.quiz_type == QuizType.EXAM,
            Quiz.status == QuizStatus.PUBLISHED,
        )
    )
    if quiz is None:
        raise ExamNotFound("Published exam quiz not found")
    return quiz


def _source_paper(db: WorkspaceSession, source_quiz_id: int) -> tuple[Paper, str]:
    quiz = assignable_exam_quiz(db, source_quiz_id)
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
        record = _record(db, assignment, submission)
        return ExamReviewView(
            assignment.id,
            assignment.title_snapshot,
            submission.submitted_at,
            submission.released_at is not None,
            exam_result(record),
            review_items(record),
        )
    if submission is None:
        return _take_view(assignment)
    if submission.released_at is None:
        return ExamPendingView(assignment.id, assignment.title_snapshot, submission.submitted_at)
    return exam_result(_record(db, assignment, submission))


def submit_exam(
    db: WorkspaceSession,
    access: StudyAccess,
    exam_id: int,
    answers: Mapping[int, str],
) -> ExamReceipt:
    if not access.can_record_progress:
        raise StudyAccessDenied("Preview cannot change learner progress")
    assignment = _locked_assignment(db, exam_id)
    paper = parse_paper(assignment.paper_version, assignment.paper_json)
    normalized = normalize_responses(paper, read_form_answers(paper, answers))
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
    normalized: dict[str, ResponseValue],
) -> ExamReceipt:
    if existing.responses_json != normalized:
        raise ExamAlreadySubmitted("This exam has already been submitted")
    return ExamReceipt(assignment.id, existing.submitted_at)


def _record_submission(
    db: WorkspaceSession, assignment: ExamAssignment, normalized: dict[str, ResponseValue]
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
    db: WorkspaceSession,
    access: StudyAccess,
    exam_id: int,
    grading: ExamGrading,
) -> ExamResultView:
    assignment, submission = _grading_target(db, access, exam_id, grading.note)
    if submission.released_at is None:
        paper = parse_paper(assignment.paper_version, assignment.paper_json)
        checked = exam_grades.validated_grades(paper, grading.grades, complete=True)
        exam_grades.save_grades(db, submission.id, checked)
        _release_submission(submission, access.actor_id, grading.note)
        db.flush()
        attempt = exam_attempt(_record(db, assignment, submission), assignment, submission)
        db.add(attempt)
        db.commit()
        question_health_service.record_attempt_evidence(db, attempt.id)
    return exam_result(_record(db, assignment, submission))


def record_grades(
    db: WorkspaceSession, access: StudyAccess, exam_id: int, grading: ExamGrading
) -> ExamRecord:
    assignment, submission = _grading_target(db, access, exam_id, grading.note)
    paper = parse_paper(assignment.paper_version, assignment.paper_json)
    checked = exam_grades.validated_grades(paper, grading.grades, complete=False)
    exam_grades.save_grades(db, submission.id, checked)
    submission.review_note = grading.note.strip()
    db.commit()
    return _record(db, assignment, submission)


def pending_grading(db: WorkspaceSession, access: StudyAccess) -> list[PendingGrading]:
    _require_reviewer(access)
    rows = db.execute(
        select(ExamAssignment, ExamSubmission)
        .join(ExamSubmission, ExamSubmission.assignment_id == ExamAssignment.id)
        .where(ExamSubmission.released_at.is_(None))
        .order_by(ExamSubmission.submitted_at, ExamAssignment.id)
    )
    return [_pending_grading(db, assignment, submission) for assignment, submission in rows]


def _pending_grading(
    db: WorkspaceSession, assignment: ExamAssignment, submission: ExamSubmission
) -> PendingGrading:
    record = _record(db, assignment, submission)
    written = {
        question.key
        for question in record.paper.questions
        if isinstance(question, OpenEndedPaperQuestion)
    }
    return PendingGrading(
        assignment.id,
        assignment.title_snapshot,
        submission.submitted_at,
        len(written),
        len(written - set(record.grades)),
    )


def unreleased_record(db: WorkspaceSession, access: StudyAccess, exam_id: int) -> ExamRecord:
    _require_reviewer(access)
    assignment = _get_assignment(db, exam_id)
    submission = _find_submission(db, assignment.id)
    if submission is None:
        raise ExamError("The learner has not submitted this exam")
    if submission.released_at is not None:
        raise ExamError("This exam result is already released")
    return _record(db, assignment, submission)


def _grading_target(
    db: WorkspaceSession, access: StudyAccess, exam_id: int, note: str
) -> tuple[ExamAssignment, ExamSubmission]:
    _require_reviewer(access)
    if len(note) > MAX_REVIEW_NOTE_LENGTH:
        raise ExamError(f"Review note must be at most {MAX_REVIEW_NOTE_LENGTH} characters")
    assignment = _get_assignment(db, exam_id)
    return assignment, _locked_submission(db, assignment.id)


def _require_reviewer(access: StudyAccess) -> None:
    if not access.can_manage_exams:
        raise StudyAccessDenied("Only a reviewer previewing a learner can grade exam results")


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


def _release_submission(submission: ExamSubmission, reviewer_id: int, note: str) -> None:
    submission.reviewed_by_id = reviewer_id
    submission.review_note = note.strip()
    submission.released_at = utc_now()


def _require_exam_view_access(access: StudyAccess) -> None:
    if access.is_preview and not access.can_manage_exams:
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
                tuple(ExamChoice(choice.key, choice.text) for choice in _choices(question)),
                isinstance(question, OpenEndedPaperQuestion),
            )
            for question in paper.questions
        ),
    )


def _choices(question: PaperQuestion) -> tuple[PaperChoice, ...]:
    return question.choices if isinstance(question, MultipleChoicePaperQuestion) else ()


def _record(
    db: WorkspaceSession, assignment: ExamAssignment, submission: ExamSubmission
) -> ExamRecord:
    paper = parse_paper(assignment.paper_version, assignment.paper_json)
    return ExamRecord(
        assignment.id,
        assignment.title_snapshot,
        paper,
        _decode_responses(paper, submission.responses_json),
        exam_grades.load_grades(db, submission.id),
        submission.review_note or "",
    )


def _decode_responses(paper: Paper, raw: object) -> dict[int, ResponseValue]:
    try:
        decoded = _response_map(raw)
        normalized = normalize_responses(paper, decoded)
        return {int(key): value for key, value in normalized.items()}
    except (TypeError, ValueError, ExamError) as exc:
        raise ExamError("This exam response is invalid") from exc


def _response_map(raw: object) -> dict[int, ResponseValue]:
    if not isinstance(raw, dict):
        raise ValueError
    decoded: dict[int, ResponseValue] = {}
    for key, value in raw.items():
        decoded[_decode_response_key(key)] = _decode_response_value(value)
    return decoded


def _decode_response_key(raw: object) -> int:
    if not isinstance(raw, str):
        raise ValueError
    return int(raw)


def _decode_response_value(raw: object) -> ResponseValue:
    if isinstance(raw, bool) or not isinstance(raw, int | str | None):
        raise ValueError
    return raw
