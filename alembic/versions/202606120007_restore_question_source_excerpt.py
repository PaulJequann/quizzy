"""restore question source excerpt

Revision ID: 202606120007
Revises: 202606120006
Create Date: 2026-06-12 00:07:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202606120007"
down_revision: str | None = "202606120006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
MARKER_TABLE = "_restore_question_source_excerpt_marker"


def upgrade() -> None:
    if not _has_column("questions", "source_excerpt"):
        op.add_column("questions", sa.Column("source_excerpt", sa.Text(), nullable=True))
        op.create_table(MARKER_TABLE, sa.Column("id", sa.Integer(), primary_key=True))


def downgrade() -> None:
    if _has_table(MARKER_TABLE) and _has_column("questions", "source_excerpt"):
        op.drop_column("questions", "source_excerpt")
        op.drop_table(MARKER_TABLE)


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()
