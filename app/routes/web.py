from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Attempt, AttemptAnswer, Difficulty, Question, Quiz, QuizStatus
from app.services.grading_service import letter_grade, passed, submit_attempt
from app.services.quiz_service import (
    create_question,
    create_quiz,
    form_question_create,
    form_quiz_create,
    get_quiz,
    list_quizzes,
    update_quiz_status,
)
from app.templating import templates

router = APIRouter()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return redirect("/quizzes")


@router.get("/quizzes", response_class=HTMLResponse)
def quiz_list(request: Request, db: Annotated[Session, Depends(get_db)]):
    quizzes = list_quizzes(db)
    return templates.TemplateResponse(
        request,
        "quizzes/list.html",
        {"quizzes": quizzes},
    )


@router.get("/quizzes/new", response_class=HTMLResponse)
def new_quiz(request: Request):
    return templates.TemplateResponse(
        request,
        "quizzes/new.html",
        {"difficulties": list(Difficulty), "error": None},
    )


@router.post("/quizzes")
def post_quiz(
    db: Annotated[Session, Depends(get_db)],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    topic: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = Difficulty.BEGINNER.value,
):
    quiz = create_quiz(db, form_quiz_create(title, description, topic, difficulty))
    return redirect(f"/quizzes/{quiz.id}/questions/new")


@router.get("/quizzes/{quiz_id}", response_class=HTMLResponse)
def quiz_detail(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return templates.TemplateResponse(
        request,
        "quizzes/detail.html",
        {"quiz": quiz, "status_options": list(QuizStatus)},
    )


@router.post("/quizzes/{quiz_id}/publish", response_class=HTMLResponse)
def publish_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    update_quiz_status(db, quiz, QuizStatus.PUBLISHED)
    return templates.TemplateResponse(request, "quizzes/_status_badge.html", {"quiz": quiz})


@router.get("/quizzes/{quiz_id}/questions/new", response_class=HTMLResponse)
def new_question(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return templates.TemplateResponse(
        request,
        "quizzes/question_form.html",
        {"quiz": quiz, "error": None},
    )


@router.post("/quizzes/{quiz_id}/questions", response_class=HTMLResponse)
def post_question(
    request: Request,
    quiz_id: int,
    db: Annotated[Session, Depends(get_db)],
    stem: Annotated[str, Form()],
    choice_1: Annotated[str, Form()],
    choice_2: Annotated[str, Form()],
    choice_3: Annotated[str, Form()],
    choice_4: Annotated[str, Form()],
    correct_choice: Annotated[int, Form()],
    explanation: Annotated[str, Form()] = "",
    source_reference: Annotated[str, Form()] = "",
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    try:
        question_data = form_question_create(
            stem=stem,
            choices=[choice_1, choice_2, choice_3, choice_4],
            correct_choice=correct_choice,
            explanation=explanation,
            source_reference=source_reference,
        )
    except ValueError as error:
        return templates.TemplateResponse(
            request,
            "quizzes/question_form.html",
            {"quiz": quiz, "error": str(error)},
            status_code=422,
        )

    create_question(db, quiz, question_data)
    if request.headers.get("HX-Request"):
        refreshed_quiz = get_quiz(db, quiz_id)
        return templates.TemplateResponse(
            request,
            "quizzes/_question_workspace.html",
            {"quiz": refreshed_quiz, "error": None},
        )
    return redirect(f"/quizzes/{quiz_id}")


@router.get("/quizzes/{quiz_id}/take", response_class=HTMLResponse)
def take_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if not quiz.questions:
        raise HTTPException(status_code=400, detail="Quiz has no questions")
    return templates.TemplateResponse(request, "quizzes/take.html", {"quiz": quiz})


@router.post("/quizzes/{quiz_id}/submit")
async def submit_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    form = await request.form()
    answers: dict[int, int | None] = {}
    for question in quiz.questions:
        raw_answer = form.get(f"question_{question.id}")
        answers[question.id] = int(raw_answer) if isinstance(raw_answer, str) else None
    attempt = submit_attempt(db, quiz, answers)
    return redirect(f"/attempts/{attempt.id}/results")


@router.get("/attempts/{attempt_id}/results", response_class=HTMLResponse)
def attempt_results(request: Request, attempt_id: int, db: Annotated[Session, Depends(get_db)]):
    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.id == attempt_id)
        .options(
            selectinload(Attempt.quiz).selectinload(Quiz.questions).selectinload(Question.choices),
            selectinload(Attempt.answers)
            .selectinload(AttemptAnswer.question)
            .selectinload(Question.choices),
            selectinload(Attempt.answers).selectinload(AttemptAnswer.selected_choice),
        )
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    answer_by_question = {answer.question_id: answer for answer in attempt.answers}
    correct_by_question = {
        question.id: next((choice for choice in question.choices if choice.is_correct), None)
        for question in attempt.quiz.questions
    }
    return templates.TemplateResponse(
        request,
        "attempts/results.html",
        {
            "attempt": attempt,
            "answer_by_question": answer_by_question,
            "correct_by_question": correct_by_question,
            "letter_grade": letter_grade(attempt.score),
            "passed": passed(attempt.score),
        },
    )
