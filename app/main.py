from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import api, scheduler_api, scheduler_web, web


def create_app() -> FastAPI:
    app = FastAPI(title="Quizzy", version="0.1.0")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(web.router)
    app.include_router(scheduler_web.router)
    app.include_router(api.router)
    app.include_router(scheduler_api.router)
    return app


app = create_app()
