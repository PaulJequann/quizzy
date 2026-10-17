from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from app.mcp.server import build_server
from app.services import generation_service
from tests.db_utils import clear_database_caches, reset_database
from tests.db_utils import get_test_session_factory as get_session_factory

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


async def _invoke(server, name: str, args: dict):
    result = await server.call_tool(name, {"workspace_id": 1, **args})
    return result.structured_content


async def _client_invoke(client, name: str, args: dict):
    result = await client.call_tool(name, {"workspace_id": 1, **args})
    return result.data


async def _ingest_note(client_or_server, path: Path, *, source_key: str) -> dict:
    args = {
        "source_key": source_key,
        "content": path.read_text(encoding="utf-8"),
        "subject_name": "AWS",
    }
    if isinstance(client_or_server, Client):
        return await _client_invoke(client_or_server, "upsert_source_document_from_content", args)
    return await _invoke(client_or_server, "upsert_source_document_from_content", args)


def test_specificity_guard_allows_existing_concept_when_source_topic_is_covered(
    mcp_database,
    tmp_path,
):
    server = build_server()
    document_id = _seed_route53_records(server, tmp_path)

    created = asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "questions": [_route53_dns_record_question(document_id, i) for i in range(3)],
            },
        )
    )
    bank = asyncio.run(_invoke(server, "list_question_bank", {"subject_name": "AWS"}))

    assert created["questions_created"] == 3
    assert len(bank["questions"]) == 4


def test_specificity_guard_saves_specific_new_concept_as_draft_review(
    mcp_database,
    tmp_path,
):
    async def run_flow():
        server = build_server()
        async with Client(server) as client:
            ingest = await _ingest_note(
                client,
                _write_route53_health_note(tmp_path / "route53-health.md"),
                source_key="aws/route53-health.md",
            )
            created = await _client_invoke(
                client,
                "create_generated_questions",
                {
                    "subject_name": "AWS",
                    "questions": [
                        _health_check_question(ingest["document"]["id"], i, specific=True)
                        for i in range(3)
                    ],
                },
            )
            concepts = await _client_invoke(client, "list_concepts", {"subject_name": "AWS"})
            bank = await _client_invoke(client, "list_question_bank", {"subject_name": "AWS"})
            return created, concepts, bank

    created, concepts, bank = asyncio.run(run_flow())

    assert created["questions_created"] == 3
    assert created["taxonomy_review"]["open_concept_review_count"] == 3
    assert concepts["concepts"] == []
    assert {question["status"] for question in bank["questions"]} == {"draft"}


def test_mcp_rejects_malformed_fresh_agent_payloads(
    mcp_database,
    tmp_path,
):
    server = build_server()
    document_id = _seed_route53_records(server, tmp_path)

    invalid_difficulty = _route53_dns_record_question(document_id) | {"difficulty": "medium"}
    with pytest.raises(Exception, match="beginner"):
        asyncio.run(
            _invoke(
                server,
                "create_generated_questions",
                {"subject_name": "AWS", "questions": [invalid_difficulty]},
            )
        )

    misnamed_source = _route53_dns_record_question(None) | {"source_ref": "route53-records.md"}
    with pytest.raises(Exception, match="source_ref"):
        asyncio.run(
            _invoke(
                server,
                "create_generated_questions",
                {"subject_name": "AWS", "questions": [misnamed_source]},
            )
        )

    invalid_source = _route53_dns_record_question(None)
    with pytest.raises(Exception, match="source_document_id or source_reference"):
        asyncio.run(
            _invoke(
                server,
                "create_generated_questions",
                {"subject_name": "AWS", "questions": [invalid_source]},
            )
        )


def test_specificity_guard_rejects_obvious_bypass_attempt(
    mcp_database,
    tmp_path,
):
    server = build_server()
    _seed_route53_records(server, tmp_path)
    health_ingest = asyncio.run(
        _ingest_note(
            server,
            _write_route53_health_note(tmp_path / "route53-health.md"),
            source_key="aws/route53-health.md",
        )
    )
    document_id = health_ingest["document"]["id"]

    created = asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "questions": [
                    _health_check_question(document_id, i, specific=False) for i in range(3)
                ],
            },
        )
    )
    reviews = asyncio.run(
        _invoke(
            server,
            "list_concept_review_queue",
            {"subject_id": health_ingest["document"]["subject_id"]},
        )
    )

    assert created["questions_created"] == 3
    assert reviews["review_items"][0]["review_type"] == "specificity_mismatch"
    assert "Route 53 Health Check" in reviews["review_items"][0]["reason"]


def _seed_route53_records(server: Any, tmp_path: Path) -> int:
    ingest = asyncio.run(
        _ingest_note(
            server,
            _write_route53_records_note(tmp_path / "route53-records.md"),
            source_key="aws/route53-records.md",
        )
    )
    document_id = ingest["document"]["id"]
    asyncio.run(_approved_create(server, [_route53_dns_record_question(document_id)]))
    return document_id


async def _approved_create(server, questions: list[dict]) -> dict:
    session_factory = get_session_factory()
    with session_factory() as db:
        return generation_service.create_generated_questions(
            db,
            generation_service.QuestionBankCreateInput(
                subject_name="AWS",
                questions=[
                    generation_service.GeneratedMultipleChoiceInput.model_validate(question)
                    for question in questions
                ],
                approve_new_concepts=True,
            ),
        )


def _write_route53_records_note(path: Path) -> Path:
    path.write_text(
        "---\ntitle: Route 53 DNS Records\nsubject: AWS\n---\n"
        "# Route 53 DNS Records\n\n"
        "Route 53 DNS records map names to values and AWS alias targets.\n",
        encoding="utf-8",
    )
    return path


def _write_route53_health_note(path: Path) -> Path:
    path.write_text(
        "---\ntitle: Route 53 Health Checks\nsubject: AWS\n---\n"
        "# Route 53 Health Checks\n\n"
        "Route 53 health checks evaluate endpoint health for failover records.\n",
        encoding="utf-8",
    )
    return path


def _route53_dns_record_question(document_id: int | None, index: int = 0) -> dict:
    return {
        "stem": f"Which Route 53 DNS record type can point at AWS resources {index}?",
        "choices": [
            {"text": "Alias record", "is_correct": True},
            {"text": "TXT record"},
            {"text": "MX record"},
            {"text": "CAA record"},
        ],
        "concept_proposals": [
            {
                "name": "Route 53 DNS Records",
                "role": "primary",
                "rationale": "Reusable concept for Route 53 DNS record behavior.",
            }
        ],
        "source_document_id": document_id,
    }


def _health_check_question(document_id: int, index: int, *, specific: bool) -> dict:
    concept_name = "Route 53 Health Checks" if specific else "Route 53 DNS Records"
    return {
        "stem": f"How should Route 53 health check behavior influence failover design {index}?",
        "choices": [
            {
                "text": "Use health check status to decide whether answers stay eligible.",
                "is_correct": True,
            },
            {"text": "Ignore health check status once the DNS record exists."},
            {"text": "Use MX priority as the health check threshold."},
            {"text": "Use TXT records to publish health check responses."},
        ],
        "explanation": "Route 53 health check behavior is a separate reusable learning objective.",
        "concept_proposals": [
            {
                "name": concept_name,
                "role": "primary",
                "rationale": f"Reusable objective for {concept_name}.",
            }
        ],
        "source_document_id": document_id,
    }
