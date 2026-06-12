from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    AttemptCreate,
    AttemptRead,
    QuestionCreate,
    QuestionRead,
    QuizCreate,
    QuizRead,
)
from app.services.grading_service import submit_attempt
from app.services.quiz_service import create_question, create_quiz, get_quiz, list_quizzes

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/quizzes", response_model=list[QuizRead])
def api_quizzes(db: Annotated[Session, Depends(get_db)]):
    return list_quizzes(db)


@router.get("/quizzes/{quiz_id}", response_model=QuizRead)
def api_quiz(quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return quiz


@router.post("/quizzes", response_model=QuizRead, status_code=201)
def api_create_quiz(payload: QuizCreate, db: Annotated[Session, Depends(get_db)]):
    return create_quiz(db, payload)


@router.post("/quizzes/{quiz_id}/questions", response_model=QuestionRead, status_code=201)
def api_create_question(
    quiz_id: int,
    payload: QuestionCreate,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    correct_count = sum(choice.is_correct for choice in payload.choices)
    if correct_count != 1:
        raise HTTPException(status_code=422, detail="Exactly one choice must be correct")
    return create_question(db, quiz, payload)


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptRead, status_code=201)
def api_create_attempt(
    quiz_id: int,
    payload: AttemptCreate,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    answers = {answer.question_id: answer.selected_choice_id for answer in payload.answers}
    return submit_attempt(db, quiz, answers)
