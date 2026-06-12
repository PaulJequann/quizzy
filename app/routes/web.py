import random
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Attempt, AttemptAnswer, Concept, Difficulty, Question, Quiz, QuizStatus
from app.services import practice_service, question_health_service
from app.services.grading_service import (
    build_concept_queue_items,
    get_mastery_rows,
    letter_grade,
    missed_concepts_for_attempt,
    passed,
    submit_attempt,
)
from app.services.quiz_service import (
    create_question,
    form_question_create,
    get_quiz,
    list_quizzes,
    update_quiz_status,
)
from app.services.subject_service import (
    build_subject_mastery_groups,
    build_subject_summaries,
    get_subject,
    get_subjects,
    quizzes_for_subject,
)
from app.templating import templates

router = APIRouter()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def redirect_to_practice(factory: Callable[[], Quiz], fallback_path: str) -> RedirectResponse:
    try:
        quiz = factory()
    except ValueError:
        return redirect(fallback_path)
    return redirect(f"/quizzes/{quiz.id}/take")


def shuffled_quiz_steps(quiz: Quiz) -> list[dict]:
    return [
        {"question": question, "choices": random.sample(question.choices, len(question.choices))}
        for question in random.sample(quiz.questions, len(quiz.questions))
    ]


def practice_builder_context(db: Session, *, error: str | None = None) -> dict:
    subjects = get_subjects(db)
    return {
        "subjects": subjects,
        "difficulties": list(Difficulty),
        "count_options": [5, 10, 20],
        "error": error,
    }


def practice_builder_input(form) -> practice_service.PracticeAssemblyInput:
    subject_id = form_int(form, "subject_id")
    if subject_id <= 0:
        raise ValueError("Choose a subject to practice.")
    concept_ids = form_int_list(form, "concept_ids")
    difficulty = form_difficulty(form_text(form, "difficulty"))
    return practice_service.PracticeAssemblyInput(
        subject_id=subject_id,
        count=form_count(form_int(form, "count", practice_service.DEFAULT_PRACTICE_COUNT)),
        strategy="concept_drill" if concept_ids else "random_subject",
        concept_ids=concept_ids or None,
        difficulty=difficulty,
    )


def form_difficulty(value: str) -> Difficulty | None:
    return Difficulty(value) if value else None


def form_count(value: int) -> int:
    return value if value in {5, 10, 20} else practice_service.DEFAULT_PRACTICE_COUNT


def form_text(form, key: str, default: str = "") -> str:
    value = form.get(key, default)
    return value if isinstance(value, str) else default


def form_int(form, key: str, default: int = 0) -> int:
    value = form.get(key, str(default))
    return int(value) if isinstance(value, str) else default


def form_int_list(form, key: str) -> list[int]:
    return [int(value) for value in form.getlist(key) if isinstance(value, str)]


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


@router.get("/mastery", response_class=HTMLResponse)
def mastery(request: Request, db: Annotated[Session, Depends(get_db)]):
    mastery_rows = get_mastery_rows(db)
    mastery_groups = build_subject_mastery_groups(mastery_rows)
    return templates.TemplateResponse(
        request,
        "mastery/index.html",
        {
            "mastery_rows": mastery_rows,
            "queue_items": build_concept_queue_items(mastery_rows),
            "mastery_groups": mastery_groups,
        },
    )


@router.post("/mastery/review")
def create_mastery_review_quiz(db: Annotated[Session, Depends(get_db)]):
    return redirect_to_practice(lambda: practice_service.assemble_mastery_review(db), "/mastery")


@router.get("/subjects", response_class=HTMLResponse)
def subject_list(request: Request, db: Annotated[Session, Depends(get_db)]):
    mastery_groups = build_subject_mastery_groups(get_mastery_rows(db))
    subjects = get_subjects(db)
    return templates.TemplateResponse(
        request,
        "subjects/list.html",
        {"subject_summaries": build_subject_summaries(subjects, mastery_groups)},
    )


@router.get("/subjects/{subject_id}", response_class=HTMLResponse)
def subject_detail(request: Request, subject_id: int, db: Annotated[Session, Depends(get_db)]):
    subject = get_subject(db, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    mastery_rows = [row for row in get_mastery_rows(db) if row.concept.subject_id == subject.id]
    mastery_groups = build_subject_mastery_groups(mastery_rows)
    mastery_group = mastery_groups[0] if mastery_groups else None
    return templates.TemplateResponse(
        request,
        "subjects/detail.html",
        {
            "subject": subject,
            "mastery_group": mastery_group,
            "quizzes": quizzes_for_subject(subject),
            "question_review_items": question_health_service.list_question_review_queue(
                db,
                subject_id=subject.id,
            ),
        },
    )


@router.post("/subjects/{subject_id}/practice")
def create_subject_practice(
    subject_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    return redirect_to_practice(
        lambda: practice_service.assemble_practice_session(
            db,
            practice_service.PracticeAssemblyInput(subject_id=subject_id),
        ),
        f"/subjects/{subject_id}",
    )


@router.post("/subjects/{subject_id}/review")
def create_subject_review_quiz(
    subject_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    subject = get_subject(db, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")

    return redirect_to_practice(
        lambda: practice_service.assemble_mastery_review(db, subject_id=subject.id),
        f"/subjects/{subject.id}",
    )


@router.post("/concepts/{concept_id}/practice")
def create_concept_practice(
    concept_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    concept = db.get(Concept, concept_id)
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    return redirect_to_practice(
        lambda: practice_service.assemble_concept_drill(
            db,
            concept_ids=[concept.id],
            count=10,
        ),
        f"/subjects/{concept.subject_id}",
    )


@router.get("/quizzes/new", response_class=HTMLResponse)
def new_quiz(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request,
        "quizzes/new.html",
        practice_builder_context(db),
    )


@router.post("/quizzes")
async def post_quiz(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    form = await request.form()
    try:
        practice_input = practice_builder_input(form)
        quiz = practice_service.assemble_practice_session(db, practice_input)
    except ValueError as error:
        return templates.TemplateResponse(
            request,
            "quizzes/new.html",
            practice_builder_context(db, error=str(error)),
            status_code=422,
        )
    return redirect(f"/quizzes/{quiz.id}/take")


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
async def post_question(
    request: Request,
    quiz_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    form = await request.form()
    try:
        question_data = form_question_create(
            stem=form_text(form, "stem"),
            choices=[
                form_text(form, "choice_1"),
                form_text(form, "choice_2"),
                form_text(form, "choice_3"),
                form_text(form, "choice_4"),
            ],
            correct_choice=form_int(form, "correct_choice"),
            explanation=form_text(form, "explanation"),
            source_reference=form_text(form, "source_reference"),
            concepts=form_text(form, "concepts"),
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
    return templates.TemplateResponse(
        request,
        "quizzes/take.html",
        {"quiz": quiz, "question_steps": shuffled_quiz_steps(quiz)},
    )


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
    question_health_service.record_attempt_evidence(db, attempt.id)
    return redirect(f"/attempts/{attempt.id}/results")


@router.get("/attempts/{attempt_id}/results", response_class=HTMLResponse)
def attempt_results(request: Request, attempt_id: int, db: Annotated[Session, Depends(get_db)]):
    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.id == attempt_id)
        .options(
            selectinload(Attempt.quiz).selectinload(Quiz.primary_subject),
            selectinload(Attempt.quiz).selectinload(Quiz.questions).selectinload(Question.choices),
            selectinload(Attempt.quiz).selectinload(Quiz.questions).selectinload(Question.concepts),
            selectinload(Attempt.quiz).selectinload(Quiz.questions).selectinload(Question.subject),
            selectinload(Attempt.answers)
            .selectinload(AttemptAnswer.question)
            .selectinload(Question.choices),
            selectinload(Attempt.answers)
            .selectinload(AttemptAnswer.question)
            .selectinload(Question.concepts),
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
            "missed_concepts": missed_concepts_for_attempt(attempt),
            "letter_grade": letter_grade(attempt.score),
            "passed": passed(attempt.score),
        },
    )
