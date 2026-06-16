from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from app.database import get_session_factory
from app.models import McpToolInvocation
from app.services import source_service


@dataclass(frozen=True)
class SourceContentToolParams:
    source_key: str
    content: str
    subject_id: int | None
    subject_name: str | None
    source_type: str
    vault_name: str | None
    display_path: str | None
    obsidian_uri: str | None
    content_sha256: str | None
    local_modified_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class McpInvocationLog:
    tool_name: str
    request_hash: str
    status: str
    client_id: str | None = None
    subject_id: int | None = None
    source_document_id: int | None = None
    source_snapshot_id: int | None = None
    error_message: str | None = None


def register_source_tools(server: FastMCP) -> None:
    _register_source_ingest_tools(server)
    _register_source_read_tools(server)


def _register_source_ingest_tools(server: FastMCP) -> None:
    def upsert_source_document_from_content(
        source_key: Annotated[
            str, Field(description="Portable source identity, such as aws/s3.md.")
        ],
        content: Annotated[str, Field(description="Full Markdown content read by the client.")],
        subject_id: Annotated[
            int | None,
            Field(description="Optional existing subject id. Takes precedence over subject_name."),
        ] = None,
        subject_name: Annotated[
            str | None,
            Field(description="Optional subject namespace when content lacks subject metadata."),
        ] = None,
        source_type: Annotated[str, Field(description="Source system label.")] = "obsidian",
        vault_name: Annotated[
            str | None,
            Field(description="Optional Obsidian vault name for obsidian:// links."),
        ] = None,
        display_path: Annotated[str | None, Field(description="Optional display path.")] = None,
        obsidian_uri: Annotated[str | None, Field(description="Optional obsidian:// URI.")] = None,
        content_sha256: Annotated[
            str | None,
            Field(description="Optional client-computed SHA-256."),
        ] = None,
        local_modified_at: Annotated[str | None, Field(description="Optional local mtime.")] = None,
        metadata: Annotated[
            dict[str, Any] | None,
            Field(description="Optional metadata. Full content is never audit-logged."),
        ] = None,
    ) -> dict[str, Any]:
        """Create or update one source document from client-supplied content."""
        return _upsert_source_document(
            SourceContentToolParams(
                source_key=source_key,
                content=content,
                subject_id=subject_id,
                subject_name=subject_name,
                source_type=source_type,
                vault_name=vault_name,
                display_path=display_path,
                obsidian_uri=obsidian_uri,
                content_sha256=content_sha256,
                local_modified_at=local_modified_at,
                metadata=metadata or {},
            )
        )

    write = {"destructiveHint": False, "idempotentHint": True}
    _register_tool(
        server,
        "upsert_source_document_from_content",
        upsert_source_document_from_content,
        annotations=write,
    )


def _upsert_source_document(params: SourceContentToolParams) -> dict[str, Any]:
    with _session_scope() as db:
        request_hash = _source_ingest_request_hash(params.source_key, params.content)
        try:
            report = source_service.upsert_source_document_from_content(
                db,
                source_service.SourceDocumentContentInput(
                    source_key=params.source_key,
                    content=params.content,
                    subject_id=params.subject_id,
                    subject_name=params.subject_name,
                    source_type=params.source_type,
                    vault_name=params.vault_name,
                    display_path=params.display_path,
                    obsidian_uri=params.obsidian_uri,
                    content_sha256=params.content_sha256,
                    local_modified_at=params.local_modified_at,
                    metadata=params.metadata,
                ),
            )
        except Exception as exc:
            db.rollback()
            _record_mcp_invocation(
                db,
                McpInvocationLog(
                    tool_name="upsert_source_document_from_content",
                    request_hash=request_hash,
                    status="error",
                    error_message=str(exc),
                ),
            )
            db.commit()
            raise
        _record_successful_ingest(db, report, request_hash)
        db.commit()
        return _ingest_report_to_dict(report)


def _register_source_read_tools(server: FastMCP) -> None:
    def list_source_documents(
        subject_name: Annotated[
            str | None,
            Field(description="Optional subject name used to filter source documents."),
        ] = None,
        limit: Annotated[int, Field(description="Maximum number of source documents.")] = 100,
    ) -> dict[str, Any]:
        """List ingested source documents without returning full note content."""
        with _session_scope() as db:
            return {
                "documents": [
                    source_service.source_document_to_dict(document)
                    for document in source_service.list_source_documents(
                        db, subject_name=subject_name, limit=limit
                    )
                ]
            }

    def find_source_documents(
        query: Annotated[
            str,
            Field(description="Text to search in title, source_key, or content."),
        ],
        limit: Annotated[int, Field(description="Maximum number of matching documents.")] = 20,
    ) -> dict[str, Any]:
        """Search ingested source documents."""
        with _session_scope() as db:
            return {
                "documents": [
                    source_service.source_document_to_dict(document)
                    for document in source_service.find_source_documents(db, query, limit=limit)
                ]
            }

    def get_source_document(
        document_id: Annotated[int, Field(description="Source document id to read.")],
    ) -> dict[str, Any]:
        """Read one ingested source document, including full Markdown content."""
        with _session_scope() as db:
            document = source_service.get_source_document(db, document_id)
            return source_service.source_document_to_dict(document, include_content=True)

    ro = {"readOnlyHint": True, "idempotentHint": True}
    _register_tool(server, "list_source_documents", list_source_documents, annotations=ro)
    _register_tool(server, "find_source_documents", find_source_documents, annotations=ro)
    _register_tool(server, "get_source_document", get_source_document, annotations=ro)


def _record_successful_ingest(
    db,
    report: source_service.SourceDocumentIngestReport,
    request_hash: str,
) -> None:
    _record_mcp_invocation(
        db,
        McpInvocationLog(
            tool_name="upsert_source_document_from_content",
            subject_id=report.document.subject_id,
            source_document_id=report.document.id,
            source_snapshot_id=report.snapshot.id,
            request_hash=request_hash,
            status="ok",
        ),
    )


def _ingest_report_to_dict(report: source_service.SourceDocumentIngestReport) -> dict[str, Any]:
    return {
        "created_document": report.created_document,
        "created_snapshot": report.created_snapshot,
        "unchanged": report.unchanged,
        "content_sha256": report.content_sha256,
        "source_snapshot_id": report.snapshot.id,
        "stale_question_count": report.stale_question_count,
        "document": source_service.source_document_to_dict(report.document),
    }


def _source_ingest_request_hash(source_key: str, content: str) -> str:
    request_body = f"{source_key}\0{content}"
    return hashlib.sha256(request_body.encode()).hexdigest()


def _record_mcp_invocation(db, invocation: McpInvocationLog) -> None:
    db.add(
        McpToolInvocation(
            tool_name=invocation.tool_name,
            client_id=invocation.client_id,
            subject_id=invocation.subject_id,
            source_document_id=invocation.source_document_id,
            source_snapshot_id=invocation.source_snapshot_id,
            request_hash=invocation.request_hash,
            status=invocation.status,
            error_message=invocation.error_message,
        )
    )


def _register_tool(
    server: FastMCP,
    name: str,
    fn: Callable[..., Any],
    *,
    annotations: dict[str, Any] | None = None,
) -> None:
    tool_annotations = ToolAnnotations(**annotations) if annotations else None
    server.add_tool(Tool.from_function(fn, name=name, annotations=tool_annotations))


@contextmanager
def _session_scope():
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
