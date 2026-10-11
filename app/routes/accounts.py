from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.auth.models import User
from app.auth.users import current_admin
from app.config import get_settings
from app.database import get_session_factory
from app.study_access import InactiveWorkspace, resolve_study_access
from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "accounts/login.html")


@router.get("/accounts")
def accounts_page(request: Request, actor: Annotated[User, Depends(current_admin)]):
    with get_session_factory()() as db:
        accounts = list(db.scalars(select(User).filter_by(is_active=True).order_by(User.id)))
    request.state.actor = actor
    return templates.TemplateResponse(request, "accounts/list.html", {"accounts": accounts})


@router.post("/accounts/select")
def select_workspace(
    actor: Annotated[User, Depends(current_admin)], workspace_id: Annotated[int, Form()]
):
    with get_session_factory()() as db:
        try:
            resolve_study_access(db, actor, workspace_id)
        except InactiveWorkspace as exc:
            raise HTTPException(404, "Account not found") from exc
    response = RedirectResponse("/quizzes", status_code=303)
    response.set_cookie(
        "quizzy_workspace",
        str(workspace_id),
        httponly=True,
        samesite="lax",
        secure=get_settings().app_base_url.startswith("https://"),
    )
    return response
