from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    model_validator,
)

from app.models import (
    AttemptRole,
    ConceptAssignmentRole,
    ConceptStatus,
    Difficulty,
    QuestionHealthState,
    QuestionStatus,
    QuestionType,
    QuizStatus,
    QuizType,
)

MIN_KEY_POINTS = 2
MAX_KEY_POINTS = 4
RUBRIC_TEXT_MAX_LENGTH = 4000

type KeyPointText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
type RubricText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=RUBRIC_TEXT_MAX_LENGTH)
]


def question_type_tag(value: object) -> str:
    raw = (
        value.get("question_type")
        if isinstance(value, dict)
        else getattr(value, "question_type", None)
    )
    return str(raw or QuestionType.MULTIPLE_CHOICE)


class MultipleChoiceFields(BaseModel):
    question_type: Literal[QuestionType.MULTIPLE_CHOICE] = QuestionType.MULTIPLE_CHOICE


class OpenEndedFields(BaseModel):
    question_type: Literal[QuestionType.OPEN_ENDED]
    model_answer: RubricText = Field(
        description="A complete answer a strong learner could give, in plain words."
    )
    grading_notes: str | None = Field(
        default=None,
        max_length=RUBRIC_TEXT_MAX_LENGTH,
        description="Acceptable variations and common misconceptions, for the grader only.",
    )
    key_points: list[KeyPointText] = Field(
        min_length=MIN_KEY_POINTS,
        max_length=MAX_KEY_POINTS,
        description=(
            "Two to four atomic ideas a correct answer must contain. Each key point is one "
            "checkable idea a grader can mark met or not met."
        ),
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


class ChoiceStudyRead(BaseModel):
    id: int
    text: str
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


class QuestionCreateBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stem: str = Field(min_length=1)
    explanation: str | None = None
    source_reference: str | None = None
    concept_proposals: list[ConceptProposalInput] = Field(default_factory=list)


class MultipleChoiceQuestionCreate(MultipleChoiceFields, QuestionCreateBase):
    choices: list[ChoiceCreate] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _one_correct_choice(self):
        if sum(choice.is_correct for choice in self.choices) != 1:
            raise ValueError("Exactly one choice must be correct")
        return self


class OpenEndedQuestionCreate(OpenEndedFields, QuestionCreateBase):
    pass


type QuestionCreate = Annotated[
    Annotated[MultipleChoiceQuestionCreate, Tag(QuestionType.MULTIPLE_CHOICE)]
    | Annotated[OpenEndedQuestionCreate, Tag(QuestionType.OPEN_ENDED)],
    Discriminator(question_type_tag),
]


class DraftQuestionEditBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stem: str = Field(min_length=1)
    explanation: str | None = None


class DraftMultipleChoiceEdit(MultipleChoiceFields, DraftQuestionEditBase):
    choices: list[ChoiceCreate] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _one_correct_choice(self):
        if sum(choice.is_correct for choice in self.choices) != 1:
            raise ValueError("Exactly one choice must be correct")
        return self


class DraftOpenEndedEdit(OpenEndedFields, DraftQuestionEditBase):
    pass


type DraftQuestionEdit = Annotated[
    Annotated[DraftMultipleChoiceEdit, Tag(QuestionType.MULTIPLE_CHOICE)]
    | Annotated[DraftOpenEndedEdit, Tag(QuestionType.OPEN_ENDED)],
    Discriminator(question_type_tag),
]


class KeyPointRead(BaseModel):
    id: int
    text: str
    order_index: int

    model_config = {"from_attributes": True}


class QuestionRead(BaseModel):
    id: int
    subject_id: int
    question_type: QuestionType
    exam_only: bool
    stem: str
    explanation: str | None
    model_answer: str | None
    grading_notes: str | None
    difficulty: Difficulty
    status: QuestionStatus
    health_state: QuestionHealthState
    health_score: float | None
    source_document_id: int | None
    source_snapshot_id: int | None
    source_reference: str | None
    source_excerpt: str | None
    generation_batch_id: int | None
    order_index: int
    subject: SubjectRead
    choices: list[ChoiceRead]
    key_points: list[KeyPointRead] = Field(default_factory=list)
    concepts: list[ConceptRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class QuestionStudyRead(BaseModel):
    id: int
    stem: str
    question_type: QuestionType
    choices: list[ChoiceStudyRead]

    model_config = {"from_attributes": True}


class QuizCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    topic: str = ""
    difficulty: Difficulty = Difficulty.BEGINNER
    status: QuizStatus = QuizStatus.DRAFT


class QuizReadBase(BaseModel):
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
    model_config = {"from_attributes": True}


class QuizRead(QuizReadBase):
    questions: list[QuestionRead] = Field(default_factory=list)


class QuizStudyRead(QuizReadBase):
    questions: list[QuestionStudyRead] = Field(default_factory=list)


class AttemptAnswerCreate(BaseModel):
    question_id: int
    selected_choice_id: int | None = None


class AttemptCreate(BaseModel):
    answers: list[AttemptAnswerCreate]


class AttemptRead(BaseModel):
    id: int
    workspace_id: int
    quiz_id: int
    actor_id: int | None
    actor_name: str | None
    actor_role: AttemptRole
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
    reported_from: str = Field(default="detailed", pattern="^(quick|detailed)$")


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
