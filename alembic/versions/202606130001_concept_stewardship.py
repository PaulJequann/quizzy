"""concept stewardship taxonomy

Revision ID: 202606130001
Revises: 202606120007
Create Date: 2026-06-13 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202606130001"
down_revision: str | None = "202606120007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(bind, name: str, values: tuple[str, ...]) -> sa.Enum:
    if bind.dialect.name == "postgresql":
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def upgrade() -> None:
    bind = op.get_bind()
    concept_status = _enum(bind, "concept_status", ("active", "proposed", "archived"))
    assignment_role = _enum(bind, "concept_assignment_role", ("primary", "secondary"))
    concept_status.create(bind, checkfirst=True)
    assignment_role.create(bind, checkfirst=True)

    _upgrade_concepts(concept_status)
    _upgrade_question_concepts(assignment_role)
    _create_decision_tables()
    _create_indexes()


def downgrade() -> None:
    _drop_indexes()
    op.drop_table("concept_aliases")
    op.drop_table("concept_review_items")
    op.drop_table("concept_decision_events")
    _downgrade_question_concepts()
    _downgrade_concepts()
    bind = op.get_bind()
    _enum(bind, "concept_assignment_role", ("primary", "secondary")).drop(bind, checkfirst=True)
    _enum(bind, "concept_status", ("active", "proposed", "archived")).drop(bind, checkfirst=True)


def _upgrade_concepts(concept_status: sa.Enum) -> None:
    op.add_column("concepts", sa.Column("normalized_name", sa.String(length=180), nullable=True))
    op.add_column(
        "concepts",
        sa.Column("status", concept_status, nullable=False, server_default="active"),
    )
    op.add_column("concepts", sa.Column("parent_concept_id", sa.Integer(), nullable=True))
    op.execute("UPDATE concepts SET normalized_name = slug WHERE normalized_name IS NULL")
    op.alter_column("concepts", "normalized_name", nullable=False)
    op.create_foreign_key(
        "fk_concepts_parent_concept_id_concepts",
        "concepts",
        "concepts",
        ["parent_concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_concepts_no_self_parent",
        "concepts",
        "parent_concept_id IS NULL OR parent_concept_id != id",
    )
    op.create_unique_constraint(
        "uq_concepts_subject_normalized_name",
        "concepts",
        ["subject_id", "normalized_name"],
    )


def _upgrade_question_concepts(assignment_role: sa.Enum) -> None:
    op.add_column(
        "question_concepts",
        sa.Column("role", assignment_role, nullable=False, server_default="secondary"),
    )
    op.add_column(
        "question_concepts",
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
    )
    op.execute("UPDATE question_concepts SET role = 'primary' WHERE is_primary IS TRUE")
    op.drop_column("question_concepts", "is_primary")


def _create_decision_tables() -> None:
    op.create_table(
        "concept_decision_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("affected_concept_ids_json", sa.Text(), nullable=False),
        sa.Column("affected_question_ids_json", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "concept_review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=True),
        sa.Column("question_id", sa.Integer(), nullable=True),
        sa.Column("review_type", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.String(length=80), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("resolution_notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "concept_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("alias_name", sa.String(length=160), nullable=False),
        sa.Column("alias_normalized_name", sa.String(length=180), nullable=False),
        sa.Column("target_concept_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_decision_event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_decision_event_id"],
            ["concept_decision_events.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_id",
            "alias_normalized_name",
            name="uq_concept_aliases_subject_alias",
        ),
    )


def _create_indexes() -> None:
    op.create_index("ix_concepts_subject_status", "concepts", ["subject_id", "status"])
    op.create_index(
        "ix_concepts_subject_parent",
        "concepts",
        ["subject_id", "parent_concept_id"],
    )
    op.create_index(
        "ix_concepts_subject_normalized",
        "concepts",
        ["subject_id", "normalized_name"],
    )
    op.create_index(
        "ix_concept_aliases_subject_alias",
        "concept_aliases",
        ["subject_id", "alias_normalized_name"],
    )
    op.create_index(
        "ix_concept_decision_events_subject_action",
        "concept_decision_events",
        ["subject_id", "action"],
    )
    op.create_index(
        "ix_concept_review_items_subject_status",
        "concept_review_items",
        ["subject_id", "status"],
    )
    op.create_index(
        "ix_question_concepts_question_role",
        "question_concepts",
        ["question_id", "role"],
    )
    op.create_index(
        "ix_question_concepts_concept_question",
        "question_concepts",
        ["concept_id", "question_id"],
    )
    op.create_index(
        "ix_attempt_answers_question_id",
        "attempt_answers",
        ["question_id"],
    )
    op.create_index(
        "ix_questions_subject_status",
        "questions",
        ["subject_id", "status"],
    )


def _drop_indexes() -> None:
    op.drop_index("ix_questions_subject_status", table_name="questions")
    op.drop_index("ix_attempt_answers_question_id", table_name="attempt_answers")
    op.drop_index("ix_question_concepts_concept_question", table_name="question_concepts")
    op.drop_index("ix_question_concepts_question_role", table_name="question_concepts")
    op.drop_index("ix_concept_review_items_subject_status", table_name="concept_review_items")
    op.drop_index("ix_concept_decision_events_subject_action", table_name="concept_decision_events")
    op.drop_index("ix_concept_aliases_subject_alias", table_name="concept_aliases")
    op.drop_index("ix_concepts_subject_normalized", table_name="concepts")
    op.drop_index("ix_concepts_subject_parent", table_name="concepts")
    op.drop_index("ix_concepts_subject_status", table_name="concepts")


def _downgrade_question_concepts() -> None:
    op.add_column(
        "question_concepts",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE question_concepts SET is_primary = TRUE WHERE role = 'primary'")
    op.drop_column("question_concepts", "rationale")
    op.drop_column("question_concepts", "role")


def _downgrade_concepts() -> None:
    op.drop_constraint("uq_concepts_subject_normalized_name", "concepts", type_="unique")
    op.drop_constraint("ck_concepts_no_self_parent", "concepts", type_="check")
    op.drop_constraint("fk_concepts_parent_concept_id_concepts", "concepts", type_="foreignkey")
    op.drop_column("concepts", "parent_concept_id")
    op.drop_column("concepts", "status")
    op.drop_column("concepts", "normalized_name")
