from contextlib import contextmanager

from sqlalchemy import Select, event, inspect, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.database import get_engine
from app.draft_policy import require_draft_content
from app.exam_policy import require_exam_separation, require_unreleased_result
from app.models import (
    Attempt,
    AttemptAnswer,
    ExamSubmission,
    Question,
    QuestionReviewItem,
    Quiz,
    QuizStatus,
    QuizType,
    WorkspaceOwned,
)
from app.study_access import StudyAccess

_attempts = Attempt.__table__
# Core columns keep the learner Attempt criterion from applying inside its own subquery.
EXAM_ATTEMPT_IDS = select(_attempts.c.id).where(_attempts.c.exam_submission_id.is_not(None))
_questions = Question.__table__
EXAM_QUESTION_IDS = select(_questions.c.id).where(_questions.c.exam_only.is_(True))


class WorkspaceSession(Session):
    def __init__(self, access: StudyAccess, *, drafts_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._access = access
        self.drafts_only = drafts_only

    @property
    def access(self) -> StudyAccess:
        return self._access


@event.listens_for(WorkspaceSession, "do_orm_execute")
def scope_reads(state: ORMExecuteState) -> None:
    if not state.is_orm_statement or not isinstance(state.statement, Select):
        raise ValueError("Workspace sessions allow ORM reads and unit-of-work writes only")
    assert isinstance(state.session, WorkspaceSession)
    access = state.session.access
    workspace_id = access.workspace_id
    state.statement = state.statement.options(
        with_loader_criteria(
            WorkspaceOwned,
            lambda row: row.workspace_id == workspace_id,
            include_aliases=True,
        )
    )
    if not access.sees_exam_content and not state.execution_options.get("include_exam_content"):
        state.statement = state.statement.options(
            with_loader_criteria(Quiz, Quiz.quiz_type != QuizType.EXAM, include_aliases=True),
            with_loader_criteria(Question, Question.exam_only.is_(False), include_aliases=True),
            with_loader_criteria(
                Attempt, Attempt.exam_submission_id.is_(None), include_aliases=True
            ),
            with_loader_criteria(
                AttemptAnswer,
                AttemptAnswer.attempt_id.not_in(EXAM_ATTEMPT_IDS),
                include_aliases=True,
            ),
            with_loader_criteria(
                QuestionReviewItem,
                QuestionReviewItem.question_id.not_in(EXAM_QUESTION_IDS),
                include_aliases=True,
            ),
        )


@event.listens_for(WorkspaceSession, "before_flush")
def scope_writes(session: WorkspaceSession, flush_context, instances) -> None:
    for row in session.new | session.dirty | session.deleted:
        _guard_write(session, row)
        _guard_attribution(session, row)
        require_unreleased_result(session, row)
    for row in session.new | session.dirty:
        require_exam_separation(session, row)
    if not session.drafts_only:
        return
    for row in session.new | session.dirty | session.deleted:
        require_draft_content(session, row)
    for row in session.new:
        if isinstance(row, Quiz):
            row.status = QuizStatus.DRAFT


def _guard_write(session: WorkspaceSession, row) -> None:
    if not isinstance(row, WorkspaceOwned):
        raise ValueError("Account data cannot be changed through a workspace session")
    _assign_owner(session, row)
    if row.workspace_id != session.access.workspace_id:
        raise ValueError("Cross-workspace write denied")
    if row in session.new:
        return
    if inspect(row).attrs.workspace_id.history.has_changes():
        raise ValueError("Ownership is immutable")


def _assign_owner(session: WorkspaceSession, row: WorkspaceOwned) -> None:
    if row in session.new and row.workspace_id is None:
        row.workspace_id = session.access.workspace_id


@contextmanager
def workspace_session(access: StudyAccess, *, drafts_only: bool = False):
    with WorkspaceSession(
        access, drafts_only=drafts_only, bind=get_engine(), expire_on_commit=False, autoflush=False
    ) as session:
        yield session


def _guard_attribution(session: WorkspaceSession, row) -> None:
    if not isinstance(row, (Attempt, ExamSubmission)):
        return
    state = inspect(row)
    if row not in session.new:
        if any(state.attrs[name].history.has_changes() for name in ("actor_id", "actor_role")):
            raise ValueError("Attribution is immutable")
        return
    if isinstance(row, Attempt) and row.exam_submission_id is not None:
        submission = next(
            (
                item
                for item in session.new
                if isinstance(item, ExamSubmission) and item.id == row.exam_submission_id
            ),
            None,
        ) or session.get(ExamSubmission, row.exam_submission_id)
        if submission is None:
            raise ValueError("Exam submission not found")
        if submission in session.new:
            _guard_attribution(session, submission)
        actor_id, actor_role = submission.actor_id, submission.actor_role
    else:
        actor_id, actor_role = session.access.actor_id, session.access.attempt_role
    for name, expected in (("actor_id", actor_id), ("actor_role", actor_role)):
        attribute = state.attrs[name]
        if attribute.history.has_changes() and getattr(row, name) != expected:
            raise ValueError("Conflicting attribution")
        setattr(row, name, expected)
