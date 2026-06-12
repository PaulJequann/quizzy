"""question first generation

Revision ID: 202606120006
Revises: 202606120005
Create Date: 2026-06-12 00:06:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202606120006"
down_revision: str | None = "202606120005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _health_state_enum(bind) -> sa.Enum:
    values = ("untested", "healthy", "needs_review", "suspect", "archived")
    if bind.dialect.name == "postgresql":
        return postgresql.ENUM(*values, name="question_health_state", create_type=False)
    return sa.Enum(*values, name="question_health_state")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE quiz_type ADD VALUE IF NOT EXISTS 'practice'")
        op.execute("ALTER TYPE difficulty ADD VALUE IF NOT EXISTS 'mixed'")

    health_state = _health_state_enum(bind)
    health_state.create(bind, checkfirst=True)

    _create_generation_batches_table()
    _add_question_first_columns(bind, health_state)
    _add_practice_columns()
    _create_question_review_items_table()
    _backfill_generation_batches(bind)


def downgrade() -> None:
    op.drop_table("question_review_items")
    op.drop_column("attempt_answers", "answered_at")
    op.drop_column("attempts", "session_type")
    op.drop_column("quiz_questions", "selection_score")
    op.drop_column("quiz_questions", "selection_reason")
    op.drop_column("quizzes", "is_disposable")
    op.drop_column("quizzes", "assembly_params_json")
    op.drop_column("quizzes", "assembly_strategy")
    op.drop_column("questions", "last_health_checked_at")
    op.drop_column("questions", "health_score")
    op.drop_column("questions", "health_state")
    op.drop_column("questions", "generation_batch_id")
    op.drop_column("questions", "source_document_id")
    op.drop_table("question_generation_batches")
    _health_state_enum(op.get_bind()).drop(op.get_bind(), checkfirst=True)


def _create_generation_batches_table() -> None:
    op.create_table(
        "question_generation_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("generator_type", sa.String(length=40), nullable=False),
        sa.Column("generator_name", sa.String(length=160), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def _add_question_first_columns(bind, health_state: sa.Enum) -> None:
    op.add_column("questions", sa.Column("source_document_id", sa.Integer(), nullable=True))
    op.add_column("questions", sa.Column("generation_batch_id", sa.Integer(), nullable=True))
    op.add_column(
        "questions",
        sa.Column("health_state", health_state, nullable=False, server_default="untested"),
    )
    op.add_column("questions", sa.Column("health_score", sa.Float(), nullable=True))
    op.add_column(
        "questions",
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_questions_source_document_id_source_documents",
            "questions",
            "source_documents",
            ["source_document_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            "fk_questions_generation_batch_id_question_generation_batches",
            "questions",
            "question_generation_batches",
            ["generation_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )


def _add_practice_columns() -> None:
    op.add_column("quizzes", sa.Column("assembly_strategy", sa.String(length=80), nullable=True))
    op.add_column(
        "quizzes",
        sa.Column("assembly_params_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "quizzes",
        sa.Column("is_disposable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "quiz_questions",
        sa.Column("selection_reason", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("quiz_questions", sa.Column("selection_score", sa.Float(), nullable=True))
    op.add_column("attempts", sa.Column("session_type", sa.String(length=40), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("UPDATE attempts SET session_type = 'standard' WHERE session_type IS NULL")
    else:
        op.execute(
            """
            UPDATE attempts
            SET session_type = quizzes.quiz_type
            FROM quizzes
            WHERE attempts.quiz_id = quizzes.id
            """
        )
        op.execute("UPDATE attempts SET session_type = 'standard' WHERE session_type IS NULL")
        op.alter_column("attempts", "session_type", nullable=False)
    op.add_column(
        "attempt_answers",
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )


def _create_question_review_items_table() -> None:
    op.create_table(
        "question_review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("generation_batch_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.String(length=80), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("resolution_notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_batch_id"],
            ["question_generation_batches.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def _backfill_generation_batches(bind) -> None:
    subject_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM subjects"))]
    for subject_id in subject_ids:
        bind.execute(
            sa.text(
                """
                INSERT INTO question_generation_batches (
                    subject_id,
                    generator_type,
                    settings_json,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (
                    :subject_id,
                    'import',
                    '{}',
                    'Synthetic batch for pre-question-first questions.',
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {"subject_id": subject_id},
        )
        batch_id = bind.execute(sa.text("SELECT MAX(id) FROM question_generation_batches")).scalar()
        bind.execute(
            sa.text(
                """
                UPDATE questions
                SET generation_batch_id = :batch_id
                WHERE subject_id = :subject_id AND generation_batch_id IS NULL
                """
            ),
            {"batch_id": batch_id, "subject_id": subject_id},
        )
