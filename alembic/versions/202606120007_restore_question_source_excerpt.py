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


def upgrade() -> None:
    if not _has_column("questions", "source_excerpt"):
        op.add_column("questions", sa.Column("source_excerpt", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("questions", "source_excerpt"):
        op.drop_column("questions", "source_excerpt")


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))
