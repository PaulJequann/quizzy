from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session_factory
from app.mcp.server import build_server
from app.models import McpToolInvocation, SourceDocument, SourceDocumentSnapshot
from app.services import generation_service, source_service
from tests.db_utils import clear_database_caches, reset_database
from tests.test_mcp_integration import (
    _client_invoke,
    _invoke,
    _s3_storage_question,
    write_note,
)

pytestmark = pytest.mark.mcp


@pytest.fixture
def db_session(test_database_url, monkeypatch):
    yield from reset_database(test_database_url, monkeypatch)
    clear_database_caches()


@pytest.fixture
def mcp_database(test_database_url, monkeypatch):
    clear_database_caches()
    for session in reset_database(test_database_url, monkeypatch):
        session.close()
        try:
            yield
        finally:
            clear_database_caches()


def test_upsert_source_document_from_content_creates_or_updates_snapshot(
    db_session: Session, tmp_path
):
    note_path = write_note(tmp_path / "s3-storage.md")

    first = _ingest_note_content(db_session, note_path, "aws/s3-storage.md")
    second = _ingest_note_content(db_session, note_path, "aws/s3-storage.md")

    documents = db_session.scalars(select(SourceDocument)).all()
    snapshots = db_session.scalars(select(SourceDocumentSnapshot)).all()
    assert len(documents) == 1
    assert len(snapshots) == 1
    assert first.created_document is True
    assert first.created_snapshot is True
    assert second.created_document is False
    assert second.created_snapshot is False
    assert second.unchanged is True
    assert documents[0].title == "S3 Storage Classes"
    assert documents[0].source_key == "aws/s3-storage.md"
    assert documents[0].subject is not None
    assert documents[0].subject.slug == "aws"
    assert "Glacier" in documents[0].content
    assert documents[0].current_snapshot_id == snapshots[0].id


def test_upsert_source_document_from_content_uses_portable_obsidian_uri(
    db_session: Session, tmp_path
):
    note_path = write_note(tmp_path / "aws" / "route53.md")

    report = _ingest_note_content(
        db_session,
        note_path,
        "aws/route53.md",
        vault_name="Study Vault",
    )

    assert report.document.obsidian_uri == ("obsidian://open?vault=Study%20Vault&file=aws/route53")


def test_upsert_source_document_from_content_bounds_extracted_title(db_session: Session):
    title = "S" * 260
    report = source_service.upsert_source_document_from_content(
        db_session,
        source_service.SourceDocumentContentInput(
            source_key="aws/long-title.md",
            content=f"---\ntitle: {title}\n---\n# Fallback\n\nBody",
            subject_name="AWS",
        ),
    )

    assert len(report.document.title) == 240
    assert report.document.title.endswith("...")


@pytest.mark.parametrize(
    ("case", "error_match"),
    [
        ("hash_mismatch", "content_sha256"),
        ("absolute_source_key", "portable key"),
        ("long_source_key", "source_key"),
    ],
)
def test_upsert_source_document_from_content_rejects_invalid_inputs(
    db_session: Session, tmp_path, case: str, error_match: str
):
    note_path = write_note(tmp_path / "s3-storage.md")

    with pytest.raises(ValueError, match=error_match):
        source_service.upsert_source_document_from_content(
            db_session,
            _invalid_ingest_input(case, note_path),
        )


def test_reingesting_changed_content_creates_new_snapshot_and_marks_stale_questions(
    db_session: Session, tmp_path
):
    note_path = write_note(tmp_path / "s3-storage.md")
    report = _ingest_note_content(db_session, note_path, "aws/s3-storage.md")
    generation_service.create_generated_questions(
        db_session,
        generation_service.QuestionBankCreateInput(
            subject_name="AWS",
            source_document_id=report.document.id,
            questions=[
                generation_service.GeneratedQuestionInput.model_validate(
                    _s3_storage_question(report.document.id)
                )
            ],
            approve_new_concepts=True,
        ),
    )
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace("Glacier", "Deep Archive"),
        encoding="utf-8",
    )

    updated = _ingest_note_content(db_session, note_path, "aws/s3-storage.md")

    assert updated.created_document is False
    assert updated.created_snapshot is True
    assert updated.stale_question_count == 1
    assert db_session.scalar(select(func.count(SourceDocumentSnapshot.id))) == 2


def test_create_questions_rejects_snapshot_from_another_source_document(
    db_session: Session, tmp_path
):
    s3_report = _ingest_note_content(db_session, write_note(tmp_path / "s3.md"), "aws/s3.md")
    route53_report = _ingest_note_content(
        db_session,
        write_note(tmp_path / "route53.md"),
        "aws/route53.md",
    )
    question = _s3_storage_question(s3_report.document.id)
    question["source_snapshot_id"] = route53_report.snapshot.id

    with pytest.raises(ValueError, match="source_snapshot_id must belong"):
        generation_service.create_generated_questions(
            db_session,
            generation_service.QuestionBankCreateInput(
                subject_name="AWS",
                questions=[generation_service.GeneratedQuestionInput.model_validate(question)],
                approve_new_concepts=True,
            ),
        )


def test_mcp_source_tools_are_content_only(mcp_database):
    server = build_server()
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}

    assert "upsert_source_document_from_content" in tool_names
    assert "ingest_obsidian_note" not in tool_names
    assert "ingest_obsidian_directory" not in tool_names


def test_mcp_content_ingestion_records_audit_log(mcp_database, tmp_path):
    server = build_server()
    note_path = write_note(tmp_path / "aws-s3.md")
    content = note_path.read_text(encoding="utf-8")
    expected_hash = hashlib.sha256(f"aws/aws-s3.md\0{content}".encode()).hexdigest()

    ingest = asyncio.run(
        _invoke(
            server,
            "upsert_source_document_from_content",
            {
                "source_key": "aws/aws-s3.md",
                "content": content,
                "subject_name": "AWS",
            },
        )
    )

    session_factory = get_session_factory()
    with session_factory() as db:
        invocation = db.scalar(select(McpToolInvocation))

    assert ingest["created_document"] is True
    assert invocation is not None
    assert invocation.tool_name == "upsert_source_document_from_content"
    assert invocation.source_document_id == ingest["document"]["id"]
    assert invocation.source_snapshot_id == ingest["source_snapshot_id"]
    assert invocation.request_hash == expected_hash
    assert invocation.status == "ok"


def test_mcp_stdio_process_upserts_source_content(mcp_database, tmp_path):
    note_path = write_note(tmp_path / "aws-s3.md")

    ingest = asyncio.run(_stdio_upsert_source(note_path))

    assert ingest["document"]["source_key"] == "aws/aws-s3.md"
    assert ingest["source_snapshot_id"] == ingest["document"]["current_snapshot_id"]


def test_http_mcp_ingests_source_and_creates_question(mcp_database, tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")
    port = _free_port()
    note_path = write_note(tmp_path / "aws-s3.md")
    process = _start_http_mcp_process(port)

    try:
        _wait_for_http_mcp_health(port)
        result = asyncio.run(_http_ingest_source_and_create_question(port, note_path))
    finally:
        _stop_process(process)

    assert result["ingest"]["document"]["source_key"] == "aws/aws-s3.md"
    assert result["created"]["questions_created"] == 1
    assert (
        result["bank"]["questions"][0]["source_document_id"] == result["ingest"]["document"]["id"]
    )
    assert (
        result["bank"]["questions"][0]["source_snapshot_id"]
        == result["ingest"]["source_snapshot_id"]
    )


def _ingest_note_content(
    db: Session,
    note_path: Path,
    source_key: str,
    *,
    vault_name: str | None = None,
):
    return source_service.upsert_source_document_from_content(
        db,
        source_service.SourceDocumentContentInput(
            source_key=source_key,
            display_path=source_key,
            content=note_path.read_text(encoding="utf-8"),
            subject_name="AWS",
            vault_name=vault_name,
        ),
    )


def _invalid_ingest_input(case: str, note_path: Path) -> source_service.SourceDocumentContentInput:
    input_by_case = {
        "hash_mismatch": source_service.SourceDocumentContentInput(
            source_key="aws/s3-storage.md",
            content=note_path.read_text(encoding="utf-8"),
            subject_name="AWS",
            content_sha256="0" * 64,
        ),
        "absolute_source_key": source_service.SourceDocumentContentInput(
            source_key=str(note_path.resolve()),
            content=note_path.read_text(encoding="utf-8"),
            subject_name="AWS",
        ),
        "long_source_key": source_service.SourceDocumentContentInput(
            source_key=f"aws/{'s' * 1024}.md",
            content=note_path.read_text(encoding="utf-8"),
            subject_name="AWS",
        ),
    }
    return input_by_case[case]


async def _stdio_upsert_source(note_path: Path) -> dict:
    transport = StdioTransport(
        command="uv",
        args=["run", "python", "mcp_server.py"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with Client(transport) as client:
        return await _client_invoke(
            client,
            "upsert_source_document_from_content",
            {
                "source_key": "aws/aws-s3.md",
                "content": note_path.read_text(encoding="utf-8"),
                "subject_name": "AWS",
            },
        )


async def _http_ingest_source_and_create_question(port: int, note_path: Path) -> dict:
    async with Client(f"http://127.0.0.1:{port}/mcp", auth="test-token") as client:
        ingest = await _client_invoke(
            client,
            "upsert_source_document_from_content",
            {
                "source_key": "aws/aws-s3.md",
                "content": note_path.read_text(encoding="utf-8"),
                "subject_name": "AWS",
            },
        )
        created = await _client_invoke(
            client,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "source_document_id": ingest["document"]["id"],
                "questions": [_s3_storage_question(ingest["document"]["id"])],
            },
        )
        bank = await _client_invoke(client, "list_question_bank", {"subject_name": "AWS"})
        return {"ingest": ingest, "created": created, "bank": bank}


def _start_http_mcp_process(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "app.mcp_http:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_http_mcp_health(port: int) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Timed out waiting for hosted MCP HTTP health check.")


def _stop_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
