from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import SourceDocument, SourceDocumentSnapshot, Subject
from app.services.quiz_service import get_or_create_subject
from app.services.text_service import slugify


@dataclass(frozen=True)
class SourceDocumentContentInput:
    source_key: str
    content: str
    subject_id: int | None = None
    subject_name: str | None = None
    source_type: str = "obsidian"
    vault_name: str | None = None
    display_path: str | None = None
    obsidian_uri: str | None = None
    content_sha256: str | None = None
    local_modified_at: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceDocumentIngestReport:
    document: SourceDocument
    snapshot: SourceDocumentSnapshot
    created_document: bool
    created_snapshot: bool
    unchanged: bool
    content_sha256: str
    stale_question_count: int


@dataclass(frozen=True)
class PreparedContentIngest:
    source_key: str
    source_type: str
    content_hash: str
    metadata: dict[str, object]
    body: str


@dataclass(frozen=True)
class DocumentUpdateContext:
    ingest_input: SourceDocumentContentInput
    prepared: PreparedContentIngest
    subject: Subject | None
    snapshot: SourceDocumentSnapshot


def upsert_source_document_from_content(
    db: Session,
    ingest_input: SourceDocumentContentInput,
) -> SourceDocumentIngestReport:
    prepared = _prepare_content_ingest(ingest_input)
    subject = _resolve_subject(
        db,
        subject_id=ingest_input.subject_id,
        subject_name=ingest_input.subject_name or _frontmatter_string(prepared.metadata, "subject"),
    )

    document = _find_document(db, prepared)
    created_document = document is None
    if document is None:
        document = _new_source_document(ingest_input, prepared)
        db.add(document)

    snapshot = _existing_snapshot(db, document, prepared.content_hash)
    created_snapshot = snapshot is None
    if snapshot is None:
        snapshot = _new_snapshot(ingest_input, prepared, document)
        db.add(snapshot)
        db.flush()

    unchanged = document.current_snapshot_id == snapshot.id
    _apply_document_update(
        document,
        DocumentUpdateContext(
            ingest_input=ingest_input,
            prepared=prepared,
            subject=subject,
            snapshot=snapshot,
        ),
    )
    stale_question_count = _stale_question_count(db, document, snapshot)
    db.commit()
    db.refresh(document)
    db.refresh(snapshot)
    return SourceDocumentIngestReport(
        document=document,
        snapshot=snapshot,
        created_document=created_document,
        created_snapshot=created_snapshot,
        unchanged=unchanged,
        content_sha256=prepared.content_hash,
        stale_question_count=stale_question_count,
    )


def _prepare_content_ingest(
    ingest_input: SourceDocumentContentInput,
) -> PreparedContentIngest:
    content_hash = _content_sha256(ingest_input.content)
    _validate_client_hash(content_hash, ingest_input.content_sha256)
    metadata, body = _metadata_for_ingest(ingest_input)
    return PreparedContentIngest(
        source_key=_normalize_source_key(ingest_input.source_key),
        source_type=_normalize_source_type(ingest_input.source_type),
        content_hash=content_hash,
        metadata=metadata,
        body=body,
    )


def _metadata_for_ingest(
    ingest_input: SourceDocumentContentInput,
) -> tuple[dict[str, object], str]:
    metadata, body = _parse_frontmatter(ingest_input.content)
    metadata = metadata | ingest_input.metadata
    if ingest_input.local_modified_at:
        metadata["local_modified_at"] = ingest_input.local_modified_at
    return metadata, body


def _find_document(
    db: Session,
    prepared: PreparedContentIngest,
) -> SourceDocument | None:
    return db.scalar(
        select(SourceDocument).where(
            SourceDocument.source_type == prepared.source_type,
            SourceDocument.source_key == prepared.source_key,
        )
    )


def _new_source_document(
    ingest_input: SourceDocumentContentInput,
    prepared: PreparedContentIngest,
) -> SourceDocument:
    return SourceDocument(
        source_type=prepared.source_type,
        source_key=prepared.source_key,
        content=ingest_input.content,
        current_content_sha256=prepared.content_hash,
        title=_title_for_content(prepared.source_key, prepared.metadata, prepared.body),
        metadata_json=json.dumps(prepared.metadata, sort_keys=True),
    )


def _new_snapshot(
    ingest_input: SourceDocumentContentInput,
    prepared: PreparedContentIngest,
    document: SourceDocument,
) -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document=document,
        content_sha256=prepared.content_hash,
        content=ingest_input.content,
        metadata_json=json.dumps(prepared.metadata, sort_keys=True),
    )


def _apply_document_update(
    document: SourceDocument,
    update: DocumentUpdateContext,
) -> None:
    document.subject = update.subject
    document.title = _title_for_content(
        update.prepared.source_key,
        update.prepared.metadata,
        update.prepared.body,
    )
    document.display_path = update.ingest_input.display_path or update.prepared.source_key
    document.obsidian_uri = update.ingest_input.obsidian_uri or _obsidian_uri(
        update.prepared.source_key,
        vault_name=update.ingest_input.vault_name,
    )
    document.content = update.ingest_input.content
    document.current_content_sha256 = update.prepared.content_hash
    document.metadata_json = json.dumps(update.prepared.metadata, sort_keys=True)
    document.current_snapshot = update.snapshot


def list_source_documents(
    db: Session, *, subject_name: str | None = None, limit: int = 100
) -> list[SourceDocument]:
    statement = (
        select(SourceDocument)
        .options(
            selectinload(SourceDocument.subject),
            selectinload(SourceDocument.current_snapshot),
        )
        .where(SourceDocument.archived_at.is_(None))
    )
    if subject_name:
        subject_slug = slugify(subject_name)
        statement = statement.join(SourceDocument.subject).where(Subject.slug == subject_slug)
    return list(db.scalars(statement.order_by(SourceDocument.updated_at.desc()).limit(limit)))


def get_source_document(db: Session, document_id: int) -> SourceDocument:
    document = db.scalar(
        select(SourceDocument)
        .where(SourceDocument.id == document_id)
        .options(
            selectinload(SourceDocument.subject),
            selectinload(SourceDocument.current_snapshot),
        )
    )
    if document is None:
        raise ValueError(f"Unknown source document id {document_id}")
    return document


def find_source_documents(db: Session, query: str, *, limit: int = 20) -> list[SourceDocument]:
    needle = f"%{query.strip()}%"
    return list(
        db.scalars(
            select(SourceDocument)
            .where(
                SourceDocument.archived_at.is_(None),
                SourceDocument.title.ilike(needle)
                | SourceDocument.source_key.ilike(needle)
                | SourceDocument.content.ilike(needle),
            )
            .options(
                selectinload(SourceDocument.subject),
                selectinload(SourceDocument.current_snapshot),
            )
            .order_by(SourceDocument.updated_at.desc())
            .limit(limit)
        )
    )


def source_document_to_dict(document: SourceDocument, *, include_content: bool = False) -> dict:
    payload = _document_summary(document)
    payload["metadata"] = json.loads(document.metadata_json or "{}")
    if include_content:
        payload["content"] = _current_content(document)
    return payload


def _document_summary(document: SourceDocument) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "source_key": document.source_key,
        "display_path": document.display_path,
        "source_type": document.source_type,
        "obsidian_uri": document.obsidian_uri,
        "subject": document.subject.name if document.subject else None,
        "subject_id": document.subject_id,
        "current_snapshot_id": document.current_snapshot_id,
        "current_content_sha256": document.current_content_sha256,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


def _current_content(document: SourceDocument) -> str:
    if document.current_snapshot is not None:
        return document.current_snapshot.content
    return document.content


def _resolve_subject(
    db: Session,
    *,
    subject_id: int | None,
    subject_name: str | None,
) -> Subject | None:
    if subject_id is not None:
        subject = db.get(Subject, subject_id)
        if subject is None:
            raise ValueError(f"Unknown subject id {subject_id}")
        return subject
    if subject_name:
        return get_or_create_subject(db, subject_name)
    return None


def _existing_snapshot(
    db: Session,
    document: SourceDocument,
    content_hash: str,
) -> SourceDocumentSnapshot | None:
    if document.id is None:
        return None
    return db.scalar(
        select(SourceDocumentSnapshot).where(
            SourceDocumentSnapshot.source_document_id == document.id,
            SourceDocumentSnapshot.content_sha256 == content_hash,
        )
    )


def _stale_question_count(
    db: Session,
    document: SourceDocument,
    snapshot: SourceDocumentSnapshot,
) -> int:
    if document.id is None or snapshot.id is None:
        return 0
    from app.models import Question

    return (
        db.scalar(
            select(func.count(Question.id)).where(
                Question.source_document_id == document.id,
                or_(
                    Question.source_snapshot_id.is_(None),
                    Question.source_snapshot_id != snapshot.id,
                ),
            )
        )
        or 0
    )


def _parse_frontmatter(content: str) -> tuple[dict[str, object], str]:
    if not content.startswith("---\n"):
        return {}, content
    _, _, rest = content.partition("---\n")
    frontmatter, separator, body = rest.partition("\n---\n")
    if not separator:
        return {}, content
    metadata: dict[str, object] = {"raw_frontmatter": frontmatter}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            metadata[key.strip()] = value.strip().strip('"')
    return metadata, body


def _title_for_content(source_key: str, metadata: dict[str, object], body: str) -> str:
    title = _frontmatter_string(metadata, "title")
    if title:
        return title
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    fallback = source_key.rstrip("/").rsplit("/", maxsplit=1)[-1].removesuffix(".md")
    return fallback.replace("-", " ").replace("_", " ").strip() or source_key


def _frontmatter_string(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _obsidian_uri(source_key: str, *, vault_name: str | None) -> str | None:
    if vault_name:
        note_key = source_key.removesuffix(".md")
        return f"obsidian://open?vault={quote(vault_name)}&file={quote(note_key)}"
    return None


def _normalize_source_type(source_type: str) -> str:
    normalized = source_type.strip().lower()
    if not normalized:
        raise ValueError("source_type is required.")
    return normalized


def _normalize_source_key(source_key: str) -> str:
    normalized = source_key.strip()
    if not normalized:
        raise ValueError("source_key is required.")
    if _looks_like_absolute_path(normalized):
        raise ValueError("source_key must be a portable key, not an absolute local path.")
    if _is_uri_source_key(normalized):
        return normalized
    return _normalize_relative_source_key(normalized)


def _is_uri_source_key(value: str) -> bool:
    return "://" in value


def _normalize_relative_source_key(value: str) -> str:
    normalized = re.sub(r"/+", "/", value.replace("\\", "/"))
    relative_key = normalized.removeprefix("./").strip("/")
    if _invalid_relative_source_key(relative_key):
        raise ValueError("source_key must be a portable relative key.")
    return relative_key


def _invalid_relative_source_key(value: str) -> bool:
    return not value or ".." in value.split("/")


def _looks_like_absolute_path(value: str) -> bool:
    return value.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_client_hash(server_hash: str, client_hash: str | None) -> None:
    if client_hash and client_hash.lower() != server_hash:
        raise ValueError("content_sha256 does not match the supplied content.")
