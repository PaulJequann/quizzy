"""confidence weighted concept scheduler

Revision ID: 202606150001
Revises: 202606130001
Create Date: 2026-06-15 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202606150001"
down_revision: str | None = "202606130001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attempt_answers",
        sa.Column("retrieval_friction_rating", sa.Integer(), nullable=True),
    )
    op.add_column("attempt_answers", sa.Column("response_time_ms", sa.Integer(), nullable=True))
    op.add_column(
        "attempt_answers",
        sa.Column("hint_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_attempt_answers_retrieval_friction_rating",
        "attempt_answers",
        "retrieval_friction_rating IS NULL OR retrieval_friction_rating IN (1, 2, 3)",
    )
    op.create_table(
        "concept_review_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ease_factor", sa.Float(), nullable=False, server_default="2.0"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "successful_spaced_reviews",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "consecutive_spaced_correct",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("lapse_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_friction_rating", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("interval_days >= 0", name="ck_concept_review_states_interval_days"),
        sa.CheckConstraint("ease_factor >= 1.3", name="ck_concept_review_states_ease_factor"),
        sa.CheckConstraint(
            "last_friction_rating IS NULL OR last_friction_rating IN (1, 2, 3)",
            name="ck_concept_review_states_last_friction_rating",
        ),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("concept_id", name="uq_concept_review_states_concept_id"),
    )


def downgrade() -> None:
    op.drop_table("concept_review_states")
    op.drop_constraint(
        "ck_attempt_answers_retrieval_friction_rating",
        "attempt_answers",
        type_="check",
    )
    op.drop_column("attempt_answers", "hint_used")
    op.drop_column("attempt_answers", "response_time_ms")
    op.drop_column("attempt_answers", "retrieval_friction_rating")
