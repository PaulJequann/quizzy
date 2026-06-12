from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QuizStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class QuizType(StrEnum):
    STANDARD = "standard"
    REVIEW = "review"
    GENERATED = "generated"


class QuestionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Subject(TimestampMixin, Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("slug", name="uq_subjects_slug"),)

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
    primary_quizzes: Mapped[list["Quiz"]] = relationship(back_populates="primary_subject")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    quiz: Mapped["Quiz"] = relationship(back_populates="question_links")
    question: Mapped["Question"] = relationship(back_populates="quiz_links")


class Quiz(TimestampMixin, Base):
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


class QuestionConcept(Base):
    __tablename__ = "question_concepts"

    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    question: Mapped["Question"] = relationship(back_populates="concept_links")
    concept: Mapped["Concept"] = relationship(back_populates="question_links")


class Concept(TimestampMixin, Base):
    __tablename__ = "concepts"
    __table_args__ = (UniqueConstraint("subject_id", "slug", name="uq_concepts_subject_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    subject: Mapped[Subject] = relationship(back_populates="concepts")
    question_links: Mapped[list[QuestionConcept]] = relationship(
        back_populates="concept",
        cascade="all, delete-orphan",
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


class Question(TimestampMixin, Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
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
    source_reference: Mapped[str | None] = mapped_column(String(255))

    subject: Mapped[Subject] = relationship(back_populates="questions")
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
    attempt_answers: Mapped[list["AttemptAnswer"]] = relationship(back_populates="question")
    concept_links: Mapped[list[QuestionConcept]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
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


class Choice(Base):
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


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    quiz: Mapped[Quiz] = relationship(back_populates="attempts")
    answers: Mapped[list["AttemptAnswer"]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

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

    attempt: Mapped[Attempt] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship(back_populates="attempt_answers")
    selected_choice: Mapped[Choice | None] = relationship(back_populates="selected_answers")
