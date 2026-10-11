from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.auth.models import User
from app.auth.users import current_user
from app.database import get_session_factory
from app.models import Quiz
from app.study_access import StudyAccess, StudyAccessDenied, StudyOperation, resolve_study_access


def study_operation(operation: StudyOperation):
    def declare[F: Callable](endpoint: F) -> F:
        if hasattr(endpoint, "study_operation"):
            raise ValueError("Study operation already declared")
        endpoint.__dict__["study_operation"] = operation
        return endpoint

    return declare


def request_access(request: Request, actor: Annotated[User, Depends(current_user)]):
    selected = request.cookies.get("quizzy_workspace")
    try:
        workspace_id = int(selected) if selected else None
    except ValueError as exc:
        raise HTTPException(400, "Invalid workspace") from exc
    try:
        with get_session_factory()() as accounts:
            access = resolve_study_access(accounts, actor, workspace_id)
    except StudyAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    request.state.actor = actor
    request.state.access = access
    _require_operation(request, access)
    return access


def _require_operation(request: Request, access: StudyAccess) -> None:
    operation = getattr(request.scope["endpoint"], "study_operation", None)
    if not isinstance(operation, StudyOperation):
        raise HTTPException(403, "Study operation required")
    if not access.allows(operation):
        if not access.can_manage_study:
            raise HTTPException(403, "Administrator access required")
        raise HTTPException(403, "Preview cannot change learner progress")


def require_published_for_learner(request: Request, quiz: Quiz) -> None:
    if not request.state.access.can_view_quiz(quiz.status):
        raise HTTPException(404, "Quiz not found")
