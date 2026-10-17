from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.boundary import request_access
from app.auth.csrf import CookieBoundaryMiddleware
from app.auth.users import auth_backend, fastapi_users
from app.routes import (
    accounts,
    api,
    exams,
    pwa,
    question_forms,
    scheduler_api,
    scheduler_web,
    teaching,
    web,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Quizzy", version="0.1.0")
    app.add_middleware(CookieBoundaryMiddleware)
    app.include_router(accounts.router)
    app.include_router(teaching.router)
    app.include_router(exams.router, dependencies=[Depends(request_access)])
    app.include_router(pwa.router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth")
    app.include_router(web.router, dependencies=[Depends(request_access)])
    app.include_router(question_forms.router, dependencies=[Depends(request_access)])
    app.include_router(scheduler_web.router, dependencies=[Depends(request_access)])
    app.include_router(api.router, dependencies=[Depends(request_access)])
    app.include_router(scheduler_api.router, dependencies=[Depends(request_access)])
    return app


app = create_app()
