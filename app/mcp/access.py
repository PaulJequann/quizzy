import asyncio
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import get_settings
from app.database import get_session_factory
from app.models import QuizStatus
from app.study_access import StudyAccess, StudyAccessDenied, resolve_study_access
from app.workspace_database import workspace_session

_call_access: ContextVar[StudyAccess] = ContextVar("mcp_workspace_access")


def load_mcp_admin(db: Session) -> User:
    admin = db.scalar(
        select(User).filter_by(
            id=get_settings().mcp_admin_user_id, is_active=True, is_superuser=True
        )
    )
    if admin is None:
        raise ValueError("MCP requires a configured administrator")
    return admin


def authorize_mcp_workspace(workspace_id: int) -> StudyAccess:
    with get_session_factory()() as db:
        actor = load_mcp_admin(db)
        try:
            return resolve_study_access(db, actor, workspace_id)
        except StudyAccessDenied as exc:
            raise ValueError("Unknown active workspace") from exc


def list_accessible_workspaces() -> dict[str, Any]:
    """Identify the configured administrator and the workspaces it may act on.

    Discovery for MCP agents: pass a returned workspace_id to every other Quizzy
    MCP tool instead of asking the user for an internal id.
    """
    with get_session_factory()() as db:
        actor = load_mcp_admin(db)
        users = db.scalars(select(User).filter_by(is_active=True).order_by(User.id)).all()
        workspaces = [
            {
                "workspace_id": user.id,
                "name": user.display_name,
                "is_admin_workspace": user.id == actor.id,
            }
            for user in users
        ]
        return {
            "administrator": {
                "workspace_id": actor.id,
                "name": actor.display_name,
            },
            "workspaces": workspaces,
            "next_action": (
                "Select the learner's workspace by name and pass its workspace_id "
                "explicitly to every other Quizzy MCP tool."
            ),
        }


def workspace_tool(fn):
    signature = inspect.signature(fn, eval_str=True)
    parameters = list(signature.parameters.values())
    parameters.append(
        inspect.Parameter("workspace_id", inspect.Parameter.KEYWORD_ONLY, annotation=int)
    )

    @wraps(fn)
    async def invoke(*args, workspace_id: int, **kwargs):
        access = authorize_mcp_workspace(workspace_id)
        status = signature.parameters.get("status")
        creates_quiz = status is not None and status.default == QuizStatus.DRAFT
        if creates_quiz and kwargs.get("status", "draft") != "draft":
            raise ValueError("MCP creates draft quizzes; publish in the browser")
        token = _call_access.set(access)
        try:
            if inspect.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return await asyncio.to_thread(fn, *args, **kwargs)
        finally:
            _call_access.reset(token)

    invoke.__dict__["__signature__"] = signature.replace(parameters=parameters)
    invoke.__annotations__ = {p.name: p.annotation for p in parameters}
    invoke.__annotations__["return"] = signature.return_annotation
    return invoke


@contextmanager
def session_scope():
    try:
        access = _call_access.get()
    except LookupError as exc:
        raise ValueError("An explicit authorized MCP workspace is required") from exc
    with workspace_session(access, drafts_only=True) as db:
        yield db
