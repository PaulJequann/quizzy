from datetime import datetime

from pydantic import BaseModel, Field

from app.models import (
    ConceptAssignmentRole,
    ConceptStatus,
    Difficulty,
    QuestionHealthState,
    QuestionStatus,
    QuizStatus,
    QuizType,
)


class ChoiceCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False


class ChoiceRead(BaseModel):
    id: int
    text: str
    is_correct: bool
    order_index: int

    model_config = {"from_attributes": True}


class SubjectRead(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConceptRead(BaseModel):
    id: int
    subject_id: int
    name: str
    slug: str
    normalized_name: str
    description: str
    status: ConceptStatus
    parent_concept_id: int | None
    subject: SubjectRead
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConceptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    parent_concept_id: int | None = None
    status: ConceptStatus = ConceptStatus.ACTIVE
    rationale: str = ""


class ConceptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    parent_concept_id: int | None = None
    rationale: str = ""


class ConceptArchiveRequest(BaseModel):
    archive_children: bool = False
    rationale: str = ""


class ConceptSplitRequest(BaseModel):
    child_names: list[str] = Field(min_length=1)
    question_ids_by_child_name: dict[str, list[int]] = Field(default_factory=dict)
    rationale: str = ""


class ConceptMergeRequest(BaseModel):
    target_concept_id: int
    source_concept_ids: list[int] = Field(min_length=1)
    dry_run: bool = True


class ConceptProposalInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    parent_name: str | None = Field(default=None, max_length=160)
    role: ConceptAssignmentRole = ConceptAssignmentRole.PRIMARY
    rationale: str = ""


class QuestionCreate(BaseModel):
    stem: str = Field(min_length=1)
    choices: list[ChoiceCreate] = Field(min_length=4, max_length=4)
    explanation: str | None = None
    source_reference: str | None = None
    concept_proposals: list[ConceptProposalInput] = Field(default_factory=list)


class QuestionRead(BaseModel):
    id: int
    subject_id: int
    stem: str
    explanation: str | None
    difficulty: Difficulty
    status: QuestionStatus
    health_state: QuestionHealthState
    health_score: float | None
    source_document_id: int | None
    source_reference: str | None
    source_excerpt: str | None
    generation_batch_id: int | None
    order_index: int
    subject: SubjectRead
    choices: list[ChoiceRead]
    concepts: list[ConceptRead] = Field(default_factory=list)

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
    primary_subject_id: int | None
    primary_subject: SubjectRead | None
    quiz_type: QuizType
    difficulty: Difficulty
    status: QuizStatus
    assembly_strategy: str | None
    assembly_params_json: str
    is_disposable: bool
    created_at: datetime
    updated_at: datetime
    questions: list[QuestionRead] = Field(default_factory=list)

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
    session_type: str
    submitted_at: datetime | None

    model_config = {"from_attributes": True}


class RetrievalFeedbackRequest(BaseModel):
    retrieval_friction_rating: int = Field(ge=1, le=3)
    response_time_ms: int | None = Field(default=None, ge=0)


class QuestionIssueReportRequest(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=2000)
    attempt_answer_id: int | None = None


class ConceptQueueItemRead(BaseModel):
    concept_id: int
    subject_id: int
    subject_name: str
    name: str
    total_attempts: int
    correct_attempts: int
    accuracy: float
    last_missed_at: datetime | None
    status: str
    missed_question_ids: list[int]
    source_references: list[str]

    model_config = {"from_attributes": True}
