from __future__ import annotations

import asyncio

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.mcp.server import build_server
from app.models import Concept, ConceptStatus, Question, Quiz, QuizStatus, Subject
from app.services import question_health_service
from app.services.concept_service import normalize_concept_name
from app.services.grading_service import submit_attempt
from app.services.quiz_service import get_or_create_subject
from tests.db_utils import clear_database_caches, get_test_session_factory, reset_database
from tests.test_mcp_integration import (
    _approved_invoke,
    _invoke,
    _mcp_ingest_note_content,
    write_note,
)

pytestmark = pytest.mark.mcp


@pytest.fixture
def mcp_database(test_database_url, monkeypatch):
    clear_database_caches()
    for session in reset_database(test_database_url, monkeypatch):
        session.close()
        try:
            yield
        finally:
            clear_database_caches()


def test_mcp_question_first_generation_practice_and_repair_queue(mcp_database, tmp_path):
    server = build_server()
    created, subject_id = _create_bank_question_with_mcp(server, tmp_path)
    practice = _assemble_mcp_practice(server, subject_id)
    question_id = created["question_ids"][0]

    assert created["questions_created"] == 1
    assert practice["quiz_type"] == "practice"
    assert practice["questions"][0]["id"] == question_id

    _miss_practice_question(practice["id"], question_id)
    queue = asyncio.run(_invoke(server, "list_question_review_queue", {"subject_id": subject_id}))
    item = queue["review_items"][0]
    context = asyncio.run(
        _invoke(
            server,
            "prepare_question_repair_context",
            {"question_id": question_id},
        )
    )
    resolved = asyncio.run(
        _invoke(
            server,
            "resolve_question_review_item",
            {"item_id": item["id"], "action": "resolved", "notes": "Reviewed in test."},
        )
    )

    assert item["suggested_action"] == "clarify_explanation"
    assert context["health"]["state"] == "needs_review"
    assert resolved["status"] == "resolved"


def test_mcp_repair_context_reads_published_question_without_writing(mcp_database, tmp_path):
    server = build_server()
    question_id, _ = _missed_published_question(server, tmp_path)
    before = _health_columns(question_id)

    context = asyncio.run(
        _invoke(server, "prepare_question_repair_context", {"question_id": question_id})
    )

    assert context["health"]["state"] == "needs_review"
    assert _health_columns(question_id) == before


def test_mcp_archived_question_leaves_practice(mcp_database, tmp_path):
    server = build_server()
    question_id, subject_id = _missed_published_question(server, tmp_path)

    archived = asyncio.run(_invoke(server, "archive_question", {"question_id": question_id}))

    assert archived["status"] == "archived"
    assert archived["health_state"] == "archived"
    with pytest.raises(ToolError, match="No active questions"):
        _assemble_mcp_practice(server, subject_id)


def _missed_published_question(server, tmp_path) -> tuple[int, int]:
    created, subject_id = _create_bank_question_with_mcp(server, tmp_path)
    practice = _assemble_mcp_practice(server, subject_id)
    question_id = created["question_ids"][0]
    with get_test_session_factory()() as db:
        quiz = db.get(Quiz, practice["id"])
        assert quiz is not None
        quiz.status = QuizStatus.PUBLISHED
        db.commit()
    _miss_practice_question(practice["id"], question_id)
    return question_id, subject_id


def _health_columns(question_id: int) -> tuple:
    with get_test_session_factory()() as db:
        question = db.get(Question, question_id)
        assert question is not None
        return question.health_state, question.health_score, question.last_health_checked_at


def _create_bank_question_with_mcp(server, tmp_path) -> tuple[dict, int]:
    note_path = write_note(tmp_path / "aws-s3.md")
    ingest = _mcp_ingest_note_content(server, note_path, source_key="aws/aws-s3.md")
    _seed_active_concept("AWS", "S3 storage classes")
    created = asyncio.run(
        _approved_invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "source_document_id": ingest["document"]["id"],
                "questions": [_bank_question_payload()],
            },
        )
    )
    bank = asyncio.run(_invoke(server, "list_question_bank", {"subject_name": "AWS"}))
    return created, bank["questions"][0]["subject_id"]


def _seed_active_concept(subject_name: str, concept_name: str) -> None:
    with get_test_session_factory()() as db:
        subject = db.scalar(select(Subject).where(Subject.name == subject_name))
        if subject is None:
            subject = get_or_create_subject(db, subject_name)
        concept = Concept(
            subject=subject,
            name=concept_name,
            slug=concept_name.replace(" ", "-"),
            normalized_name=normalize_concept_name(concept_name, subject_name=subject.name),
            status=ConceptStatus.ACTIVE,
        )
        db.add(concept)
        db.commit()


def _bank_question_payload() -> dict:
    return {
        "stem": "Which S3 storage class is intended for archival data?",
        "choices": [
            {"text": "S3 Glacier", "is_correct": True},
            {"text": "S3 Standard because it is always cheapest"},
            {"text": "CloudFront because it stores all origin data permanently"},
            {"text": "IAM because policies retain objects"},
        ],
        "explanation": "Glacier is the archival storage class.",
        "concept_proposals": [
            {
                "name": "S3 storage classes",
                "role": "primary",
                "rationale": "Reusable S3 storage-class learning objective.",
            }
        ],
    }


def _assemble_mcp_practice(server, subject_id: int) -> dict:
    return asyncio.run(
        _invoke(server, "assemble_practice_session", {"subject_id": subject_id, "count": 1})
    )


def _miss_practice_question(practice_id: int, question_id: int) -> None:
    with get_test_session_factory()() as db:
        quiz = db.get(Quiz, practice_id)
        assert quiz is not None
        question = db.get(Question, question_id)
        assert question is not None
        wrong_choice = next(choice for choice in question.choices if not choice.is_correct)
        attempt = submit_attempt(db, quiz, {question.id: wrong_choice.id})
        question_health_service.record_attempt_evidence(db, attempt.id)
