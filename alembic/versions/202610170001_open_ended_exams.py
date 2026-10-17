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
    _create_response_grades()
    _add_exam_attempts()


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


def _create_response_grades() -> None:
    op.create_table(
        "exam_response_grades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("question_key", sa.Integer(), nullable=False),
        sa.Column("key_points_met", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("misconception", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(key_points_met) = 'array'", name="ck_exam_response_grades_met_array"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "submission_id"],
            ["exam_submissions.workspace_id", "exam_submissions.id"],
            ondelete="CASCADE",
            name="fk_exam_response_grades_submission_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["users.id"], name="fk_exam_response_grades_workspace"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "submission_id", "question_key", name="uq_exam_response_grade_question"
        ),
    )
    op.create_index(
        "ix_exam_response_grades_workspace_id", "exam_response_grades", ["workspace_id"]
    )


def _add_exam_attempts() -> None:
    op.add_column("attempts", sa.Column("exam_submission_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_attempts_exam_submission", "attempts", ["exam_submission_id"])
    op.create_foreign_key(
        "fk_attempts_exam_submission_workspace",
        "attempts",
        "exam_submissions",
        ["workspace_id", "exam_submission_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("attempt_answers", sa.Column("score", sa.Float(), nullable=True))
    op.execute("UPDATE attempt_answers SET score = CASE WHEN is_correct THEN 1 ELSE 0 END")
    op.alter_column("attempt_answers", "score", nullable=False)
    op.create_check_constraint(
        "ck_attempt_answers_score_range", "attempt_answers", "score >= 0 AND score <= 1"
    )
    op.add_column(
        "attempt_answers", sa.Column("misconception", sa.String(length=200), nullable=True)
    )


def downgrade() -> None:
    op.execute("DELETE FROM attempts WHERE exam_submission_id IS NOT NULL")
    op.drop_column("attempt_answers", "misconception")
    op.drop_constraint("ck_attempt_answers_score_range", "attempt_answers", type_="check")
    op.drop_column("attempt_answers", "score")
    op.drop_constraint("fk_attempts_exam_submission_workspace", "attempts", type_="foreignkey")
    op.drop_constraint("uq_attempts_exam_submission", "attempts", type_="unique")
    op.drop_column("attempts", "exam_submission_id")
    op.drop_index("ix_exam_response_grades_workspace_id", table_name="exam_response_grades")
    op.drop_table("exam_response_grades")
    op.drop_index("ix_question_key_points_workspace_id", table_name="question_key_points")
    op.drop_table("question_key_points")
    op.drop_constraint("ck_questions_open_ended_exam_only", "questions", type_="check")
    op.drop_constraint("ck_questions_model_answer_type", "questions", type_="check")
    op.drop_column("questions", "grading_notes")
    op.drop_column("questions", "model_answer")
    op.drop_column("questions", "exam_only")
    op.drop_column("questions", "question_type")
    QUESTION_TYPE.drop(op.get_bind(), checkfirst=True)
