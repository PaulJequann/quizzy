from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202610160001"
down_revision: str | None = "202610110001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exam_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by_id", sa.Integer(), nullable=False),
        sa.Column("source_quiz_id", sa.Integer(), nullable=False),
        sa.Column("title_snapshot", sa.String(length=200), nullable=False),
        sa.Column("paper_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("paper_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(paper_json) = 'array' AND jsonb_array_length(paper_json) > 0",
            name="ck_exam_assignments_paper_array",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["users.id"], name="fk_exam_assignments_workspace"
        ),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_exam_assignments_workspace_id"),
        sa.UniqueConstraint(
            "assigned_by_id", "workspace_id", "request_key", name="uq_exam_assignment_request"
        ),
    )
    op.create_index("ix_exam_assignments_workspace_id", "exam_assignments", ["workspace_id"])
    op.create_table(
        "exam_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("responses_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(responses_json) = 'object'", name="ck_exam_responses_object"
        ),
        sa.CheckConstraint(
            "(released_at IS NULL AND reviewed_by_id IS NULL) OR "
            "(released_at IS NOT NULL AND reviewed_by_id IS NOT NULL)",
            name="ck_exam_submission_release_pair",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "assignment_id"],
            ["exam_assignments.workspace_id", "exam_assignments.id"],
            ondelete="CASCADE",
            name="fk_exam_submissions_assignment_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["users.id"], name="fk_exam_submissions_workspace"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_exam_submissions_workspace_id"),
        sa.UniqueConstraint("assignment_id", name="uq_exam_submission_assignment"),
    )
    op.create_index("ix_exam_submissions_workspace_id", "exam_submissions", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_exam_submissions_workspace_id", table_name="exam_submissions")
    op.drop_table("exam_submissions")
    op.drop_index("ix_exam_assignments_workspace_id", table_name="exam_assignments")
    op.drop_table("exam_assignments")
