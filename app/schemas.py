from datetime import datetime

from pydantic import BaseModel, Field

from app.models import Difficulty, QuizStatus


class ChoiceCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False


class ChoiceRead(BaseModel):
    id: int
    text: str
    is_correct: bool
    order_index: int

    model_config = {"from_attributes": True}


class QuestionCreate(BaseModel):
    stem: str = Field(min_length=1)
    choices: list[ChoiceCreate] = Field(min_length=4, max_length=4)
    explanation: str | None = None
    source_reference: str | None = None


class QuestionRead(BaseModel):
    id: int
    stem: str
    explanation: str | None
    source_reference: str | None
    order_index: int
    choices: list[ChoiceRead]

    model_config = {"from_attributes": True}


class QuizCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    topic: str = ""
    difficulty: Difficulty = Difficulty.BEGINNER
    status: QuizStatus = QuizStatus.DRAFT


class QuizRead(BaseModel):
    id: int
    title: str
    description: str
    topic: str
    difficulty: Difficulty
    status: QuizStatus
    created_at: datetime
    updated_at: datetime
    questions: list[QuestionRead] = []

    model_config = {"from_attributes": True}


class AttemptAnswerCreate(BaseModel):
    question_id: int
    selected_choice_id: int | None = None


class AttemptCreate(BaseModel):
    answers: list[AttemptAnswerCreate]


class AttemptRead(BaseModel):
    id: int
    quiz_id: int
    score: float
    total_questions: int
    correct_count: int
    submitted_at: datetime | None

    model_config = {"from_attributes": True}
