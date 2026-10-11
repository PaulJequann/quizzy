"""preserve learner question feedback history

Revision ID: 202607280001
Revises: 202606160001
Create Date: 2026-07-28 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202607280001"
down_revision: str | None = "202606160001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_feedback_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("review_item_id", sa.Integer(), nullable=False),
        sa.Column("attempt_answer_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("reported_from", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_answer_id"], ["attempt_answers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["review_item_id"], ["question_review_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_question_feedback_events_review_item_created_at",
        "question_feedback_events",
        ["review_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_question_feedback_events_review_item_created_at")
    op.drop_table("question_feedback_events")
