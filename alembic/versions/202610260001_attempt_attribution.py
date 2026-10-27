from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202610260001"
down_revision: str | None = "202610170001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("attempts", "exam_submissions"):
        op.add_column(table, sa.Column("actor_id", sa.Integer(), nullable=True))
        op.add_column(
            table, sa.Column("actor_role", sa.String(7), nullable=False, server_default="unknown")
        )
        op.create_foreign_key(f"fk_{table}_actor_id_users", table, "users", ["actor_id"], ["id"])
        op.create_check_constraint(
            f"ck_{table}_attribution",
            table,
            "(actor_role = 'unknown' AND actor_id IS NULL) OR "
            "(actor_role = 'learner' AND actor_id IS NOT NULL AND actor_id = workspace_id) OR "
            "(actor_role = 'test' AND actor_id IS NOT NULL)",
        )
        op.alter_column(table, "actor_role", server_default=None)
    op.execute(
        "UPDATE concept_review_states SET interval_days = 1, ease_factor = 2.0, "
        "due_at = NULL, last_review_at = NULL, successful_spaced_reviews = 0, "
        "consecutive_spaced_correct = 0, lapse_count = 0, last_friction_rating = NULL"
    )
    op.execute(
        "UPDATE questions SET health_state = CASE "
        "WHEN status = 'archived' OR health_state = 'archived' "
        "THEN 'archived'::question_health_state "
        "WHEN EXISTS (SELECT 1 FROM question_feedback_events f "
        "JOIN question_review_items r ON r.id = f.review_item_id "
        "WHERE f.question_id = questions.id AND r.status = 'open') THEN health_state "
        "ELSE 'untested'::question_health_state END, "
        "health_score = NULL, last_health_checked_at = NULL"
    )


def downgrade() -> None:
    for table in ("attempts", "exam_submissions"):
        op.drop_constraint(f"ck_{table}_attribution", table, type_="check")
        op.drop_constraint(f"fk_{table}_actor_id_users", table, type_="foreignkey")
        op.drop_column(table, "actor_role")
        op.drop_column(table, "actor_id")
