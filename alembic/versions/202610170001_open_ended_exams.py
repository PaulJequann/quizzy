from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202610170001"
down_revision: str | None = "202610160001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUESTION_TYPE = postgresql.ENUM("multiple_choice", "open_ended", name="question_type")


def upgrade() -> None:
    op.execute("ALTER TYPE quiz_type ADD VALUE IF NOT EXISTS 'exam'")
    QUESTION_TYPE.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "questions",
        sa.Column(
            "question_type",
            postgresql.ENUM(name="question_type", create_type=False),
            nullable=False,
            server_default="multiple_choice",
        ),
    )
    op.add_column(
        "questions",
        sa.Column("exam_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("questions", sa.Column("model_answer", sa.Text(), nullable=True))
    op.add_column("questions", sa.Column("grading_notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_questions_model_answer_type",
        "questions",
        "(question_type = 'open_ended') = (model_answer IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_questions_open_ended_exam_only",
        "questions",
        "question_type != 'open_ended' OR exam_only",
    )
    _create_key_points()


def _create_key_points() -> None:
    op.create_table(
        "question_key_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["users.id"], name="fk_question_key_points_workspace"
        ),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "question_id"],
            ["questions.workspace_id", "questions.id"],
            ondelete="CASCADE",
            name="fk_question_key_points_question_id_workspace",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_question_key_points_workspace_id"),
    )
    op.create_index("ix_question_key_points_workspace_id", "question_key_points", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_question_key_points_workspace_id", table_name="question_key_points")
    op.drop_table("question_key_points")
    op.drop_constraint("ck_questions_open_ended_exam_only", "questions", type_="check")
    op.drop_constraint("ck_questions_model_answer_type", "questions", type_="check")
    op.drop_column("questions", "grading_notes")
    op.drop_column("questions", "model_answer")
    op.drop_column("questions", "exam_only")
    op.drop_column("questions", "question_type")
    QUESTION_TYPE.drop(op.get_bind(), checkfirst=True)
