from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.boundary import study_operation
from app.database import get_db
from app.models import Question, QuestionType, Quiz, QuizType
from app.routes.web import form_int, form_text, redirect
from app.services.quiz_service import (
    QuestionFormInput,
    create_question,
    form_question_create,
    get_quiz,
    question_form_values,
    update_question,
)
from app.study_access import StudyOperation
from app.templating import templates

router = APIRouter()


@router.get("/quizzes/{quiz_id}/questions/new", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def new_question(
    request: Request,
    quiz_id: int,
    db: Annotated[Session, Depends(get_db)],
    question_type: str = "",
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return templates.TemplateResponse(
        request,
        "quizzes/question_form.html",
        {"quiz": quiz, "error": None, "question_type": _form_question_type(quiz, question_type)},
    )


@router.post("/quizzes/{quiz_id}/questions", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
async def post_question(
    request: Request,
    quiz_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    form = await request.form()
    question_type = _form_question_type(quiz, form_text(form, "question_type"))
    try:
        requested = QuestionType(form_text(form, "question_type", QuestionType.MULTIPLE_CHOICE))
        create_question(db, quiz, form_question_create(_question_form_input(form, requested)))
    except ValueError as error:
        return templates.TemplateResponse(
            request,
            "quizzes/question_form.html",
            {"quiz": quiz, "error": str(error), "question_type": question_type},
            status_code=422,
        )

    if request.headers.get("HX-Request"):
        refreshed_quiz = get_quiz(db, quiz_id)
        return templates.TemplateResponse(
            request,
            "quizzes/_question_workspace.html",
            {"quiz": refreshed_quiz, "error": None, "question_type": question_type},
        )
    return redirect(f"/quizzes/{quiz_id}")


@router.get("/quizzes/{quiz_id}/questions/{question_id}/edit", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def edit_question(
    request: Request, quiz_id: int, question_id: int, db: Annotated[Session, Depends(get_db)]
):
    quiz, question = _quiz_question(db, quiz_id, question_id)
    return _edit_form(request, QuestionEdit(quiz, question.id, question_form_values(question)))


@router.post("/quizzes/{quiz_id}/questions/{question_id}", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
async def post_question_edit(
    request: Request, quiz_id: int, question_id: int, db: Annotated[Session, Depends(get_db)]
):
    quiz, question = _quiz_question(db, quiz_id, question_id)
    form = await request.form()
    values = None
    try:
        values = _question_form_input(form, question.question_type)
        update_question(db, question, form_question_create(values))
    except ValueError as error:
        db.rollback()
        shown = values or question_form_values(question)
        return _edit_form(request, QuestionEdit(quiz, question_id, shown, str(error)))
    return redirect(f"/quizzes/{quiz_id}")


def _quiz_question(db: Session, quiz_id: int, question_id: int) -> tuple[Quiz, Question]:
    quiz = get_quiz(db, quiz_id)
    question = (
        next((item for item in quiz.questions if item.id == question_id), None) if quiz else None
    )
    if quiz is None or question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return quiz, question


@dataclass(frozen=True)
class QuestionEdit:
    quiz: Quiz
    question_id: int
    values: QuestionFormInput
    error: str | None = None


def _edit_form(request: Request, edit: QuestionEdit):
    return templates.TemplateResponse(
        request,
        "quizzes/question_edit.html",
        {
            "quiz": edit.quiz,
            "question_id": edit.question_id,
            "error": edit.error,
            "question_type": edit.values.question_type,
            "values": edit.values,
            "editing": True,
        },
        status_code=422 if edit.error else 200,
    )


def _form_question_type(quiz: Quiz, requested: str) -> QuestionType:
    if quiz.quiz_type == QuizType.EXAM and requested == QuestionType.OPEN_ENDED:
        return QuestionType.OPEN_ENDED
    return QuestionType.MULTIPLE_CHOICE


def _question_form_input(form, question_type: QuestionType) -> QuestionFormInput:
    return QuestionFormInput(
        stem=form_text(form, "stem"),
        choices=[form_text(form, f"choice_{number}") for number in range(1, 5)],
        correct_choice=form_int(form, "correct_choice"),
        explanation=form_text(form, "explanation"),
        source_reference=form_text(form, "source_reference"),
        concepts=form_text(form, "concepts"),
        question_type=question_type,
        model_answer=form_text(form, "model_answer"),
        grading_notes=form_text(form, "grading_notes") or None,
        key_points=_numbered_fields(form, "key_point_"),
    )


def _numbered_fields(form, prefix: str) -> list[str]:
    suffixes = [key.removeprefix(prefix) for key in form if key.startswith(prefix)]
    numbers = sorted(int(suffix) for suffix in suffixes if suffix.isdigit())
    return [form_text(form, f"{prefix}{number}") for number in numbers]
