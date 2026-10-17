from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.auth.models import User  # noqa: F401
from app.database import Base


class QuizStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class QuizType(StrEnum):
    STANDARD = "standard"
    REVIEW = "review"
    GENERATED = "generated"
    PRACTICE = "practice"
    EXAM = "exam"


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    OPEN_ENDED = "open_ended"


class QuestionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class QuestionHealthState(StrEnum):
    UNTESTED = "untested"
    HEALTHY = "healthy"
    NEEDS_REVIEW = "needs_review"
    SUSPECT = "suspect"
    ARCHIVED = "archived"


class ConceptStatus(StrEnum):
    ACTIVE = "active"
    PROPOSED = "proposed"
    ARCHIVED = "archived"


class ConceptAssignmentRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    MIXED = "mixed"


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class WorkspaceOwned:
    workspace_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)


class AppSetting(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "app_settings"

    workspace_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Subject(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_subjects_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    concepts: Mapped[list["Concept"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
        order_by="Concept.name",
    )
    questions: Mapped[list["Question"]] = relationship(back_populates="subject")
    generation_batches: Mapped[list["QuestionGenerationBatch"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
    )
    question_review_items: Mapped[list["QuestionReviewItem"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
    )
    concept_review_items: Mapped[list["ConceptReviewItem"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
    )
    concept_decision_events: Mapped[list["ConceptDecisionEvent"]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
    )
    primary_quizzes: Mapped[list["Quiz"]] = relationship(back_populates="primary_subject")
    source_documents: Mapped[list["SourceDocument"]] = relationship(back_populates="subject")


class SourceDocument(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_type", "source_key", name="uq_source_documents_type_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"))
    current_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "source_document_snapshots.id",
            name="fk_source_documents_current_snapshot_id",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="obsidian")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    display_path: Mapped[str | None] = mapped_column(String(1024))
    obsidian_uri: Mapped[str | None] = mapped_column(String(1024))
    current_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped[Subject | None] = relationship(back_populates="source_documents")
    questions: Mapped[list["Question"]] = relationship(back_populates="source_document")
    generation_batches: Mapped[list["QuestionGenerationBatch"]] = relationship(
        back_populates="source_document"
    )
    snapshots: Mapped[list["SourceDocumentSnapshot"]] = relationship(
        back_populates="source_document",
        cascade="all, delete-orphan",
        foreign_keys="SourceDocumentSnapshot.source_document_id",
        order_by="SourceDocumentSnapshot.created_at.desc()",
    )
    current_snapshot: Mapped["SourceDocumentSnapshot | None"] = relationship(
        foreign_keys=[current_snapshot_id],
        post_update=True,
    )


class SourceDocumentSnapshot(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "source_document_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "content_sha256",
            name="uq_source_document_snapshots_document_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    source_document: Mapped[SourceDocument] = relationship(
        back_populates="snapshots",
        foreign_keys=[source_document_id],
    )
    questions: Mapped[list["Question"]] = relationship(back_populates="source_snapshot")


class McpToolInvocation(WorkspaceOwned, Base):
    __tablename__ = "mcp_tool_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(160))
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"))
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    source_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_document_snapshots.id", ondelete="SET NULL")
    )
    request_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class QuestionGenerationBatch(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "question_generation_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    generator_type: Mapped[str] = mapped_column(String(40), nullable=False, default="mcp_agent")
    generator_name: Mapped[str | None] = mapped_column(String(160))
    settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    subject: Mapped[Subject] = relationship(back_populates="generation_batches")
    source_document: Mapped[SourceDocument | None] = relationship(
        back_populates="generation_batches"
    )
    questions: Mapped[list["Question"]] = relationship(back_populates="generation_batch")
    review_items: Mapped[list["QuestionReviewItem"]] = relationship(
        back_populates="generation_batch"
    )


class QuizQuestion(WorkspaceOwned, Base):
    __tablename__ = "quiz_questions"

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    selection_score: Mapped[float | None] = mapped_column(Float)

    quiz: Mapped["Quiz"] = relationship(back_populates="question_links")
    question: Mapped["Question"] = relationship(back_populates="quiz_links")


class Quiz(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    quiz_type: Mapped[QuizType] = mapped_column(
        Enum(
            QuizType,
            name="quiz_type",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=QuizType.STANDARD,
    )
    difficulty: Mapped[Difficulty] = mapped_column(
        Enum(
            Difficulty,
            name="difficulty",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=Difficulty.BEGINNER,
    )
    status: Mapped[QuizStatus] = mapped_column(
        Enum(
            QuizStatus,
            name="quiz_status",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=QuizStatus.DRAFT,
    )
    assembly_strategy: Mapped[str | None] = mapped_column(String(80))
    assembly_params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_disposable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    question_links: Mapped[list[QuizQuestion]] = relationship(
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="QuizQuestion.order_index",
    )
    questions: Mapped[list["Question"]] = relationship(
        secondary="quiz_questions",
        order_by="QuizQuestion.order_index",
        viewonly=True,
    )
    primary_subject: Mapped[Subject | None] = relationship(back_populates="primary_quizzes")
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan"
    )

    @property
    def topic(self) -> str:
        return self.primary_subject.name if self.primary_subject else ""


class QuestionConcept(WorkspaceOwned, Base):
    __tablename__ = "question_concepts"
    __table_args__ = (Index("ix_question_concepts_question_role", "question_id", "role"),)

    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[ConceptAssignmentRole] = mapped_column(
        Enum(
            ConceptAssignmentRole,
            name="concept_assignment_role",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=ConceptAssignmentRole.SECONDARY,
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    question: Mapped["Question"] = relationship(back_populates="concept_links")
    concept: Mapped["Concept"] = relationship(back_populates="question_links")

    @property
    def is_primary(self) -> bool:
        return self.role == ConceptAssignmentRole.PRIMARY

    @is_primary.setter
    def is_primary(self, value: bool) -> None:
        self.role = ConceptAssignmentRole.PRIMARY if value else ConceptAssignmentRole.SECONDARY


class Concept(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint("subject_id", "slug", name="uq_concepts_subject_slug"),
        UniqueConstraint(
            "subject_id",
            "normalized_name",
            name="uq_concepts_subject_normalized_name",
        ),
        CheckConstraint(
            "parent_concept_id IS NULL OR parent_concept_id != id",
            name="ck_concepts_no_self_parent",
        ),
        Index("ix_concepts_subject_status", "subject_id", "status"),
        Index("ix_concepts_subject_parent", "subject_id", "parent_concept_id"),
        Index("ix_concepts_subject_normalized", "subject_id", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ConceptStatus] = mapped_column(
        Enum(
            ConceptStatus,
            name="concept_status",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=ConceptStatus.ACTIVE,
    )
    parent_concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL")
    )

    subject: Mapped[Subject] = relationship(back_populates="concepts")
    parent: Mapped["Concept | None"] = relationship(
        back_populates="children",
        remote_side="Concept.id",
    )
    children: Mapped[list["Concept"]] = relationship(
        back_populates="parent",
        order_by="Concept.name",
    )
    question_links: Mapped[list[QuestionConcept]] = relationship(
        back_populates="concept",
        cascade="all, delete-orphan",
    )
    review_state: Mapped["ConceptReviewState | None"] = relationship(
        back_populates="concept",
        cascade="all, delete-orphan",
        uselist=False,
    )
    questions: Mapped[list["Question"]] = relationship(
        "Question",
        secondary="question_concepts",
        back_populates="concepts",
        viewonly=True,
    )

    @property
    def topic(self) -> str:
        return self.subject.name


class ConceptReviewState(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "concept_review_states"
    __table_args__ = (
        UniqueConstraint("concept_id", name="uq_concept_review_states_concept_id"),
        CheckConstraint("interval_days >= 0", name="ck_concept_review_states_interval_days"),
        CheckConstraint("ease_factor >= 1.3", name="ck_concept_review_states_ease_factor"),
        CheckConstraint(
            "last_friction_rating IS NULL OR last_friction_rating IN (1, 2, 3)",
            name="ck_concept_review_states_last_friction_rating",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ease_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    successful_spaced_reviews: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_spaced_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lapse_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_friction_rating: Mapped[int | None] = mapped_column(Integer)

    concept: Mapped[Concept] = relationship(back_populates="review_state")


class ConceptAlias(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "concept_aliases"
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "alias_normalized_name",
            name="uq_concept_aliases_subject_alias",
        ),
        Index("ix_concept_aliases_subject_alias", "subject_id", "alias_normalized_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    alias_name: Mapped[str] = mapped_column(String(160), nullable=False)
    alias_normalized_name: Mapped[str] = mapped_column(String(180), nullable=False)
    target_concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_decision_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("concept_decision_events.id", ondelete="SET NULL")
    )

    subject: Mapped[Subject] = relationship()
    target_concept: Mapped[Concept] = relationship(foreign_keys=[target_concept_id])
    source_decision_event: Mapped["ConceptDecisionEvent | None"] = relationship(
        foreign_keys=[source_decision_event_id]
    )


class ConceptDecisionEvent(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "concept_decision_events"
    __table_args__ = (Index("ix_concept_decision_events_subject_action", "subject_id", "action"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    affected_concept_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    affected_question_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(80), nullable=False, default="app")
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="web")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    subject: Mapped[Subject] = relationship(back_populates="concept_decision_events")


class ConceptReviewItem(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "concept_review_items"
    __table_args__ = (Index("ix_concept_review_items_subject_status", "subject_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id", ondelete="SET NULL"))
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    review_type: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    resolution_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    subject: Mapped[Subject] = relationship(back_populates="concept_review_items")
    concept: Mapped[Concept | None] = relationship()
    question: Mapped["Question | None"] = relationship()


class Question(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "(question_type = 'open_ended') = (model_answer IS NOT NULL)",
            name="ck_questions_model_answer_type",
        ),
        CheckConstraint(
            "question_type != 'open_ended' OR exam_only",
            name="ck_questions_open_ended_exam_only",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    question_type: Mapped[QuestionType] = mapped_column(
        Enum(
            QuestionType,
            name="question_type",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=QuestionType.MULTIPLE_CHOICE,
    )
    exam_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    model_answer: Mapped[str | None] = mapped_column(Text)
    grading_notes: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[Difficulty] = mapped_column(
        Enum(
            Difficulty,
            name="difficulty",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=Difficulty.BEGINNER,
    )
    status: Mapped[QuestionStatus] = mapped_column(
        Enum(
            QuestionStatus,
            name="question_status",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=QuestionStatus.ACTIVE,
    )
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    source_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_document_snapshots.id", ondelete="SET NULL")
    )
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    generation_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_generation_batches.id", ondelete="SET NULL")
    )
    health_state: Mapped[QuestionHealthState] = mapped_column(
        Enum(
            QuestionHealthState,
            name="question_health_state",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=QuestionHealthState.UNTESTED,
    )
    health_score: Mapped[float | None] = mapped_column(Float)
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped[Subject] = relationship(back_populates="questions")
    source_document: Mapped[SourceDocument | None] = relationship(back_populates="questions")
    source_snapshot: Mapped[SourceDocumentSnapshot | None] = relationship(
        back_populates="questions"
    )
    generation_batch: Mapped[QuestionGenerationBatch | None] = relationship(
        back_populates="questions"
    )
    quiz_links: Mapped[list[QuizQuestion]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
    )
    quizzes: Mapped[list[Quiz]] = relationship(secondary="quiz_questions", viewonly=True)
    choices: Mapped[list["Choice"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="Choice.order_index",
    )
    key_points: Mapped[list["QuestionKeyPoint"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionKeyPoint.order_index",
    )
    attempt_answers: Mapped[list["AttemptAnswer"]] = relationship(back_populates="question")
    concept_links: Mapped[list[QuestionConcept]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
    )
    review_items: Mapped[list["QuestionReviewItem"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
    )
    feedback_events: Mapped[list["QuestionFeedbackEvent"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionFeedbackEvent.created_at",
    )
    concepts: Mapped[list[Concept]] = relationship(
        secondary="question_concepts",
        back_populates="questions",
        order_by="Concept.name",
        viewonly=True,
    )

    @property
    def order_index(self) -> int:
        if not self.quiz_links:
            return 0
        return min(link.order_index for link in self.quiz_links)


class Choice(WorkspaceOwned, Base):
    __tablename__ = "choices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question: Mapped[Question] = relationship(back_populates="choices")
    selected_answers: Mapped[list["AttemptAnswer"]] = relationship(back_populates="selected_choice")


class QuestionKeyPoint(WorkspaceOwned, Base):
    __tablename__ = "question_key_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question: Mapped[Question] = relationship(back_populates="key_points")


class QuestionReviewItem(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "question_review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    generation_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_generation_batches.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    resolution_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    question: Mapped[Question] = relationship(back_populates="review_items")
    subject: Mapped[Subject] = relationship(back_populates="question_review_items")
    generation_batch: Mapped[QuestionGenerationBatch | None] = relationship(
        back_populates="review_items"
    )
    feedback_events: Mapped[list["QuestionFeedbackEvent"]] = relationship(
        back_populates="review_item",
        cascade="all, delete-orphan",
        order_by="QuestionFeedbackEvent.created_at",
    )


class QuestionFeedbackEvent(WorkspaceOwned, TimestampMixin, Base):
    __tablename__ = "question_feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    review_item_id: Mapped[int] = mapped_column(
        ForeignKey("question_review_items.id", ondelete="CASCADE"), nullable=False
    )
    attempt_answer_id: Mapped[int | None] = mapped_column(
        ForeignKey("attempt_answers.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reported_from: Mapped[str] = mapped_column(String(40), nullable=False)

    question: Mapped[Question] = relationship(back_populates="feedback_events")
    review_item: Mapped[QuestionReviewItem] = relationship(back_populates="feedback_events")
    attempt_answer: Mapped["AttemptAnswer | None"] = relationship()


class Attempt(WorkspaceOwned, Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    session_type: Mapped[str] = mapped_column(String(40), nullable=False, default="standard")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    quiz: Mapped[Quiz] = relationship(back_populates="attempts")
    answers: Mapped[list["AttemptAnswer"]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class AttemptAnswer(WorkspaceOwned, Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (
        CheckConstraint(
            "retrieval_friction_rating IS NULL OR retrieval_friction_rating IN (1, 2, 3)",
            name="ck_attempt_answers_retrieval_friction_rating",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    selected_choice_id: Mapped[int | None] = mapped_column(
        ForeignKey("choices.id", ondelete="SET NULL")
    )
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    question_stem_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    selected_choice_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correct_choice_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utc_now)
    retrieval_friction_rating: Mapped[int | None] = mapped_column(Integer)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    hint_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    attempt: Mapped[Attempt] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship(back_populates="attempt_answers")
    selected_choice: Mapped[Choice | None] = relationship(back_populates="selected_answers")


class ExamAssignment(WorkspaceOwned, Base):
    __tablename__ = "exam_assignments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_exam_assignments_workspace_id"),
        UniqueConstraint(
            "assigned_by_id", "workspace_id", "request_key", name="uq_exam_assignment_request"
        ),
        CheckConstraint(
            "jsonb_typeof(paper_json) = 'array' AND jsonb_array_length(paper_json) > 0",
            name="ck_exam_assignments_paper_array",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_quiz_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    paper_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    paper_json: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    request_key: Mapped[UUID] = mapped_column(nullable=False)


class ExamSubmission(WorkspaceOwned, Base):
    __tablename__ = "exam_submissions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_exam_submissions_workspace_id"),
        UniqueConstraint("assignment_id", name="uq_exam_submission_assignment"),
        ForeignKeyConstraint(
            ["workspace_id", "assignment_id"],
            ["exam_assignments.workspace_id", "exam_assignments.id"],
            ondelete="CASCADE",
            name="fk_exam_submissions_assignment_workspace",
        ),
        CheckConstraint("jsonb_typeof(responses_json) = 'object'", name="ck_exam_responses_object"),
        CheckConstraint(
            "(released_at IS NULL AND reviewed_by_id IS NULL) OR "
            "(released_at IS NOT NULL AND reviewed_by_id IS NOT NULL)",
            name="ck_exam_submission_release_pair",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    responses_json: Mapped[dict[str, int | None]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
