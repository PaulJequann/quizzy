from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import SourceDocument, Subject
from app.services.quiz_service import get_or_create_subject
from app.services.text_service import slugify


@dataclass(frozen=True)
class SourceDocumentIngestReport:
    document: SourceDocument
    created: bool


@dataclass(frozen=True)
class ObsidianDirectoryIngestInput:
    path: str | Path
    subject_name: str | None = None
    glob: str = "**/*.md"
    vault_name: str | None = None


def ingest_obsidian_note(
    db: Session,
    path: str | Path,
    *,
    subject_name: str | None = None,
    vault_name: str | None = None,
) -> SourceDocumentIngestReport:
    note_path = _resolve_note_path(path)
    _ensure_path_allowed(note_path)
    content = note_path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(content)
    title = _title_for_note(note_path, metadata, body)
    inferred_subject = subject_name or _frontmatter_string(metadata, "subject")
    subject = get_or_create_subject(db, inferred_subject) if inferred_subject else None
    normalized_path = str(note_path)
    existing = db.scalar(select(SourceDocument).where(SourceDocument.path == normalized_path))

    if existing is None:
        document = SourceDocument(path=normalized_path)
        db.add(document)
        created = True
    else:
        document = existing
        created = False

    document.subject = subject
    document.source_type = "obsidian"
    document.title = title
    document.obsidian_uri = _obsidian_uri(note_path, vault_name=vault_name)
    document.content = content
    document.metadata_json = json.dumps(metadata, sort_keys=True)
    db.commit()
    db.refresh(document)
    return SourceDocumentIngestReport(document=document, created=created)


def ingest_obsidian_directory(
    db: Session,
    ingest_input: ObsidianDirectoryIngestInput,
) -> dict[str, object]:
    directory = Path(ingest_input.path).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        raise ValueError(f"Obsidian directory not found: {directory}")
    _ensure_path_allowed(directory)

    processed: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []
    for note_path in sorted(directory.glob(ingest_input.glob)):
        if not note_path.is_file() or note_path.suffix.casefold() != ".md":
            continue
        try:
            report = ingest_obsidian_note(
                db,
                note_path,
                subject_name=ingest_input.subject_name,
                vault_name=ingest_input.vault_name,
            )
        except Exception as exc:
            db.rollback()
            skipped.append({"path": str(note_path), "reason": str(exc)})
            continue
        processed.append(_document_summary(report.document) | {"created": report.created})

    return {
        "directory": str(directory),
        "files_processed": len(processed),
        "files_skipped": skipped,
        "documents": processed,
    }


def list_source_documents(
    db: Session, *, subject_name: str | None = None, limit: int = 100
) -> list[SourceDocument]:
    statement = select(SourceDocument).options(selectinload(SourceDocument.subject))
    if subject_name:
        subject_slug = slugify(subject_name)
        statement = statement.join(SourceDocument.subject).where(Subject.slug == subject_slug)
    return list(db.scalars(statement.order_by(SourceDocument.updated_at.desc()).limit(limit)))


def get_source_document(db: Session, document_id: int) -> SourceDocument:
    document = db.scalar(
        select(SourceDocument)
        .where(SourceDocument.id == document_id)
        .options(selectinload(SourceDocument.subject))
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
                SourceDocument.title.ilike(needle)
                | SourceDocument.path.ilike(needle)
                | SourceDocument.content.ilike(needle)
            )
            .options(selectinload(SourceDocument.subject))
            .order_by(SourceDocument.updated_at.desc())
            .limit(limit)
        )
    )


def source_document_to_dict(document: SourceDocument, *, include_content: bool = False) -> dict:
    payload = _document_summary(document)
    payload["metadata"] = json.loads(document.metadata_json or "{}")
    if include_content:
        payload["content"] = document.content
    return payload


def _document_summary(document: SourceDocument) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "path": document.path,
        "source_type": document.source_type,
        "obsidian_uri": document.obsidian_uri,
        "subject": document.subject.name if document.subject else None,
        "subject_id": document.subject_id,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


def _resolve_note_path(path: str | Path) -> Path:
    note_path = Path(path).expanduser().resolve()
    if not note_path.exists() or not note_path.is_file():
        raise ValueError(f"Obsidian note not found: {note_path}")
    if note_path.suffix.casefold() != ".md":
        raise ValueError(f"Obsidian note must be a Markdown file: {note_path}")
    return note_path


def _ensure_path_allowed(path: Path) -> None:
    roots = _vault_roots()
    if not roots:
        return
    if not any(path == root or root in path.parents for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise ValueError(f"Path is outside configured Obsidian vault roots: {allowed}")


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


def _title_for_note(path: Path, metadata: dict[str, object], body: str) -> str:
    title = _frontmatter_string(metadata, "title")
    if title:
        return title
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ").strip() or path.stem


def _frontmatter_string(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _obsidian_uri(path: Path, *, vault_name: str | None) -> str:
    if vault_name:
        note_path = _vault_relative_note_path(path) or path.stem
        return f"obsidian://open?vault={quote(vault_name)}&file={quote(note_path)}"
    return f"obsidian://open?path={quote(str(path))}"


def _vault_roots() -> list[Path]:
    return [
        Path(raw.strip()).expanduser().resolve()
        for raw in get_settings().obsidian_vault_roots.split(",")
        if raw.strip()
    ]


def _vault_relative_note_path(path: Path) -> str | None:
    for root in _vault_roots():
        if path == root or root in path.parents:
            return path.relative_to(root).with_suffix("").as_posix()
    return None
