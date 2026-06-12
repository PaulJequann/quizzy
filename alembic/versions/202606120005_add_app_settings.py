"""add app settings

Revision ID: 202606120005
Revises: 202606120004
Create Date: 2026-06-12 00:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202606120005"
down_revision: str | None = "202606120004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value, created_at, updated_at)
        VALUES ('default_difficulty', 'intermediate', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )


def downgrade() -> None:
    op.drop_table("app_settings")
