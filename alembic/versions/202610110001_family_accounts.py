import secrets

import sqlalchemy as sa
from fastapi_users.password import PasswordHelper

from alembic import op

revision = "202610110001"
down_revision = "202607280001"
branch_labels = None
depends_on = None

PRIVATE_TABLES = (
    "app_settings",
    "subjects",
    "source_documents",
    "source_document_snapshots",
    "mcp_tool_invocations",
    "question_generation_batches",
    "quiz_questions",
    "quizzes",
    "question_concepts",
    "concepts",
    "concept_review_states",
    "concept_aliases",
    "concept_decision_events",
    "concept_review_items",
    "questions",
    "choices",
    "question_review_items",
    "question_feedback_events",
    "attempts",
    "attempt_answers",
)

ID_TABLES = (
    "subjects",
    "source_documents",
    "source_document_snapshots",
    "mcp_tool_invocations",
    "question_generation_batches",
    "quizzes",
    "concepts",
    "concept_review_states",
    "concept_aliases",
    "concept_decision_events",
    "concept_review_items",
    "questions",
    "choices",
    "question_review_items",
    "question_feedback_events",
    "attempts",
    "attempt_answers",
)

PRIVATE_REFERENCES = [
    ("source_documents", "current_snapshot_id", "source_document_snapshots", "SET NULL"),
    ("source_documents", "subject_id", "subjects", "SET NULL"),
    ("source_document_snapshots", "source_document_id", "source_documents", "CASCADE"),
    ("mcp_tool_invocations", "source_document_id", "source_documents", "SET NULL"),
    ("mcp_tool_invocations", "subject_id", "subjects", "SET NULL"),
    ("mcp_tool_invocations", "source_snapshot_id", "source_document_snapshots", "SET NULL"),
    ("question_generation_batches", "subject_id", "subjects", "CASCADE"),
    ("question_generation_batches", "source_document_id", "source_documents", "SET NULL"),
    ("quiz_questions", "quiz_id", "quizzes", "CASCADE"),
    ("quiz_questions", "question_id", "questions", "CASCADE"),
    ("quizzes", "primary_subject_id", "subjects", "SET NULL"),
    ("question_concepts", "question_id", "questions", "CASCADE"),
    ("question_concepts", "concept_id", "concepts", "CASCADE"),
    ("concepts", "parent_concept_id", "concepts", "SET NULL"),
    ("concepts", "subject_id", "subjects", "CASCADE"),
    ("concept_review_states", "concept_id", "concepts", "CASCADE"),
    ("concept_aliases", "source_decision_event_id", "concept_decision_events", "SET NULL"),
    ("concept_aliases", "target_concept_id", "concepts", "CASCADE"),
    ("concept_aliases", "subject_id", "subjects", "CASCADE"),
    ("concept_decision_events", "subject_id", "subjects", "CASCADE"),
    ("concept_review_items", "question_id", "questions", "CASCADE"),
    ("concept_review_items", "concept_id", "concepts", "SET NULL"),
    ("concept_review_items", "subject_id", "subjects", "CASCADE"),
    ("questions", "source_snapshot_id", "source_document_snapshots", "SET NULL"),
    ("questions", "source_document_id", "source_documents", "SET NULL"),
    ("questions", "subject_id", "subjects", "RESTRICT"),
    ("questions", "generation_batch_id", "question_generation_batches", "SET NULL"),
    ("choices", "question_id", "questions", "CASCADE"),
    ("question_review_items", "generation_batch_id", "question_generation_batches", "SET NULL"),
    ("question_review_items", "subject_id", "subjects", "CASCADE"),
    ("question_review_items", "question_id", "questions", "CASCADE"),
    ("question_feedback_events", "attempt_answer_id", "attempt_answers", "SET NULL"),
    ("question_feedback_events", "review_item_id", "question_review_items", "CASCADE"),
    ("question_feedback_events", "question_id", "questions", "CASCADE"),
    ("attempts", "quiz_id", "quizzes", "CASCADE"),
    ("attempt_answers", "selected_choice_id", "choices", "SET NULL"),
    ("attempt_answers", "question_id", "questions", "CASCADE"),
    ("attempt_answers", "attempt_id", "attempts", "CASCADE"),
]


def upgrade():
    _check_legacy_choices()
    _create_accounts()
    for table in PRIVATE_TABLES:
        op.add_column(table, sa.Column("workspace_id", sa.Integer(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table} SET workspace_id = (SELECT id FROM users WHERE email = :email)"
            ).bindparams(email="legacy-parent@invalid.local")
        )
        op.alter_column(table, "workspace_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_workspace", table, "users", ["workspace_id"], ["id"])
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    for table in ID_TABLES:
        op.create_unique_constraint(f"uq_{table}_workspace_id", table, ["workspace_id", "id"])
    for table, column, target, action in PRIVATE_REFERENCES:
        delete_action = f"SET NULL ({column})" if action == "SET NULL" else action
        op.create_foreign_key(
            f"fk_{table}_{column}_workspace",
            table,
            target,
            ["workspace_id", column],
            ["workspace_id", "id"],
            ondelete=delete_action,
        )
    op.drop_constraint("uq_subjects_slug", "subjects", type_="unique")
    op.create_unique_constraint("uq_subjects_slug", "subjects", ["workspace_id", "slug"])
    op.drop_constraint("uq_source_documents_type_key", "source_documents", type_="unique")
    op.create_unique_constraint(
        "uq_source_documents_type_key",
        "source_documents",
        ["workspace_id", "source_type", "source_key"],
    )
    op.drop_constraint("app_settings_pkey", "app_settings", type_="primary")
    op.create_primary_key("app_settings_pkey", "app_settings", ["workspace_id", "key"])
    op.create_unique_constraint("uq_choices_question_id", "choices", ["question_id", "id"])
    op.create_foreign_key(
        "fk_answer_choice_question",
        "attempt_answers",
        "choices",
        ["question_id", "selected_choice_id"],
        ["question_id", "id"],
        ondelete="SET NULL (selected_choice_id)",
    )


def downgrade():
    raise RuntimeError("Family ownership cannot be removed without an explicit data export.")


def _check_legacy_choices():
    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT a.id FROM attempt_answers a JOIN choices c ON c.id = a.selected_choice_id "
                "WHERE c.question_id <> a.question_id ORDER BY a.id LIMIT 10"
            )
        )
        .scalars()
        .all()
    )
    if invalid:
        raise RuntimeError(
            f"Legacy selected choices belong to different questions for answer IDs {invalid}. "
            "Review these answers and repair their selected_choice_id before upgrading. "
            "The migration has not changed your rows."
        )


def _create_accounts():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "access_tokens",
        sa.Column("token", sa.String(43), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_access_tokens_created_at", "access_tokens", ["created_at"])
    op.execute(
        sa.text(
            "INSERT INTO users "
            "(email, hashed_password, display_name, is_active, is_superuser, is_verified) "
            "VALUES ('legacy-parent@invalid.local', :password, 'Parent', false, true, false)"
        ).bindparams(password=PasswordHelper().hash(secrets.token_urlsafe(64)))
    )
