from contextlib import contextmanager

from sqlalchemy import Select, event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.database import get_engine
from app.draft_policy import require_draft_content
from app.models import Quiz, QuizStatus, WorkspaceOwned
from app.study_access import StudyAccess


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
    workspace_id = state.session.access.workspace_id
    state.statement = state.statement.options(
        with_loader_criteria(
            WorkspaceOwned,
            lambda row: row.workspace_id == workspace_id,
            include_aliases=True,
        )
    )


@event.listens_for(WorkspaceSession, "before_flush")
def scope_writes(session: WorkspaceSession, flush_context, instances) -> None:
    for row in session.new | session.dirty | session.deleted:
        _guard_write(session, row)
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
