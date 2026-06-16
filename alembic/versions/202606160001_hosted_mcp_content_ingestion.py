"""hosted mcp content ingestion

Revision ID: 202606160001
Revises: 202606150001
Create Date: 2026-06-16 00:01:00
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "202606160001"
down_revision: str | None = "202606150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _upgrade_source_document_identity()
    _create_source_document_snapshots()
    _backfill_source_snapshots()
    _finalize_source_document_constraints()
    _add_question_snapshot_link()
    _create_mcp_invocation_log()


def _upgrade_source_document_identity() -> None:
    op.drop_constraint("uq_source_documents_path", "source_documents", type_="unique")
    op.alter_column(
        "source_documents",
        "path",
        new_column_name="source_key",
        existing_type=sa.String(length=1024),
        existing_nullable=False,
    )
    op.add_column("source_documents", sa.Column("current_snapshot_id", sa.Integer()))
    op.add_column("source_documents", sa.Column("display_path", sa.String(length=1024)))
    op.add_column("source_documents", sa.Column("current_content_sha256", sa.String(length=64)))
    op.add_column("source_documents", sa.Column("archived_at", sa.DateTime(timezone=True)))


def _create_source_document_snapshots() -> None:
    op.create_table(
        "source_document_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_source_document_snapshots_source_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id",
            "content_sha256",
            name="uq_source_document_snapshots_document_hash",
        ),
    )


def _finalize_source_document_constraints() -> None:
    op.alter_column(
        "source_documents",
        "current_content_sha256",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_source_documents_current_snapshot_id",
        "source_documents",
        "source_document_snapshots",
        ["current_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_source_documents_type_key",
        "source_documents",
        ["source_type", "source_key"],
    )


def _add_question_snapshot_link() -> None:
    op.add_column("questions", sa.Column("source_snapshot_id", sa.Integer()))
    op.create_foreign_key(
        "fk_questions_source_snapshot_id",
        "questions",
        "source_document_snapshots",
        ["source_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )


def _create_mcp_invocation_log() -> None:
    op.create_table(
        "mcp_tool_invocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("client_id", sa.String(length=160)),
        sa.Column("subject_id", sa.Integer()),
        sa.Column("source_document_id", sa.Integer()),
        sa.Column("source_snapshot_id", sa.Integer()),
        sa.Column("request_hash", sa.String(length=64)),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["source_document_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("mcp_tool_invocations")
    op.drop_constraint("fk_questions_source_snapshot_id", "questions", type_="foreignkey")
    op.drop_column("questions", "source_snapshot_id")

    op.drop_constraint(
        "uq_source_documents_type_key",
        "source_documents",
        type_="unique",
    )
    op.drop_constraint(
        "fk_source_documents_current_snapshot_id",
        "source_documents",
        type_="foreignkey",
    )
    op.drop_table("source_document_snapshots")
    op.drop_column("source_documents", "archived_at")
    op.drop_column("source_documents", "current_content_sha256")
    op.drop_column("source_documents", "display_path")
    op.drop_column("source_documents", "current_snapshot_id")
    op.alter_column(
        "source_documents",
        "source_key",
        new_column_name="path",
        existing_type=sa.String(length=1024),
        existing_nullable=False,
    )
    op.create_unique_constraint("uq_source_documents_path", "source_documents", ["path"])


def _backfill_source_snapshots() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, content, metadata_json FROM source_documents ORDER BY id")
    ).mappings()
    now = datetime.now(UTC)
    for row in rows:
        content = row["content"]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        snapshot_id = connection.execute(
            sa.text(
                """
                INSERT INTO source_document_snapshots (
                    source_document_id,
                    content_sha256,
                    content,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    :source_document_id,
                    :content_sha256,
                    :content,
                    :metadata_json,
                    :created_at,
                    :updated_at
                )
                RETURNING id
                """
            ),
            {
                "source_document_id": row["id"],
                "content_sha256": content_hash,
                "content": content,
                "metadata_json": row["metadata_json"],
                "created_at": now,
                "updated_at": now,
            },
        ).scalar_one()
        connection.execute(
            sa.text(
                """
                UPDATE source_documents
                SET display_path = source_key,
                    current_content_sha256 = :content_sha256,
                    current_snapshot_id = :snapshot_id
                WHERE id = :source_document_id
                """
            ),
            {
                "content_sha256": content_hash,
                "snapshot_id": snapshot_id,
                "source_document_id": row["id"],
            },
        )
