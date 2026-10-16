from dataclasses import dataclass
from enum import Enum, auto

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.models import QuizStatus


class StudyOperation(Enum):
    VIEW = auto()
    MANAGE = auto()
    RECORD_PROGRESS = auto()
    MANAGE_AND_RECORD = auto()
    ASSIGN_EXAM = auto()
    REVIEW_EXAM = auto()


@dataclass(frozen=True)
class StudyAccess:
    actor_id: int
    workspace_id: int
    can_manage_study: bool
    workspace_name: str
    actor_is_superuser: bool = False

    @property
    def is_preview(self) -> bool:
        return self.actor_id != self.workspace_id

    @property
    def can_record_progress(self) -> bool:
        return not self.is_preview

    @property
    def can_manage_and_record(self) -> bool:
        return self.can_manage_study and self.can_record_progress

    @property
    def can_assign_exams(self) -> bool:
        return self.actor_is_superuser and not self.is_preview

    @property
    def can_review_exams(self) -> bool:
        return self.actor_is_superuser and self.is_preview

    def allows(self, operation: StudyOperation) -> bool:
        return {
            StudyOperation.VIEW: True,
            StudyOperation.MANAGE: self.can_manage_study,
            StudyOperation.RECORD_PROGRESS: self.can_record_progress,
            StudyOperation.MANAGE_AND_RECORD: self.can_manage_and_record,
            StudyOperation.ASSIGN_EXAM: self.can_assign_exams,
            StudyOperation.REVIEW_EXAM: self.can_review_exams,
        }.get(operation, False)

    def can_view_quiz(self, status: QuizStatus) -> bool:
        return self.can_manage_study or status == QuizStatus.PUBLISHED

    def can_report_question(self, attempt_answer_id: int | None) -> bool:
        return self.can_record_progress and (self.can_manage_study or attempt_answer_id is not None)


class StudyAccessDenied(ValueError):
    pass


class InactiveWorkspace(StudyAccessDenied):
    pass


def resolve_study_access(
    accounts: Session, actor: User, requested_id: int | None = None
) -> StudyAccess:
    if not actor.is_active:
        raise StudyAccessDenied("Inactive account")
    workspace_id = requested_id if requested_id is not None else actor.id
    if workspace_id != actor.id and not actor.is_superuser:
        raise StudyAccessDenied("Workspace access denied")
    target = accounts.scalar(select(User).filter_by(id=workspace_id, is_active=True))
    if target is None:
        raise InactiveWorkspace("Inactive workspace")
    return StudyAccess(
        actor.id,
        workspace_id,
        _can_manage_workspace(actor, workspace_id),
        target.display_name,
        actor.is_superuser,
    )


def _can_manage_workspace(actor: User, workspace_id: int) -> bool:
    return actor.is_superuser or actor.id == workspace_id
