"""make questions reusable across quizzes

Revision ID: 202606120002
Revises: 202606120001
Create Date: 2026-06-12 00:02:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202606120002"
down_revision: str | None = "202606120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quiz_questions",
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("quiz_id", "question_id"),
    )
    op.execute(
        """
        INSERT INTO quiz_questions (quiz_id, question_id, order_index)
        SELECT quiz_id, id, order_index
        FROM questions
        """
    )

    with op.batch_alter_table("questions") as batch_op:
        batch_op.drop_column("quiz_id")
        batch_op.drop_column("order_index")


def downgrade() -> None:
    with op.batch_alter_table("questions") as batch_op:
        batch_op.add_column(sa.Column("quiz_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0")
        )

    op.execute(
        """
        UPDATE questions
        SET quiz_id = (
            SELECT quiz_questions.quiz_id
            FROM quiz_questions
            WHERE quiz_questions.question_id = questions.id
            ORDER BY quiz_questions.quiz_id
            LIMIT 1
        ),
        order_index = COALESCE((
            SELECT quiz_questions.order_index
            FROM quiz_questions
            WHERE quiz_questions.question_id = questions.id
            ORDER BY quiz_questions.quiz_id
            LIMIT 1
        ), 0)
        """
    )
    op.drop_table("quiz_questions")
