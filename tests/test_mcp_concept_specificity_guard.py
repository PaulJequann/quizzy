from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult

from app.mcp.server import build_server
from tests.db_utils import clear_database_caches, reset_database

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
    result = await server.call_tool(name, args)
    return result.structured_content


async def _client_invoke(client, name: str, args: dict):
    result = await client.call_tool(name, args)
    return result.data


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


def test_specificity_guard_allows_specific_new_concept_through_elicitation(
    mcp_database,
    tmp_path,
):
    async def approve_concepts(message, response_type, params, context):
        approval_messages.append(message)
        return ElicitResult(action="accept", content=response_type(value=True))

    async def run_flow():
        server = build_server()
        async with Client(server, elicitation_handler=approve_concepts) as client:
            ingest = await _client_invoke(
                client,
                "ingest_obsidian_note",
                {
                    "path": str(_write_route53_health_note(tmp_path / "route53-health.md")),
                    "subject_name": "AWS",
                },
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
            return created, concepts

    approval_messages: list[str] = []
    created, concepts = asyncio.run(run_flow())

    assert created["questions_created"] == 3
    assert "Route 53 Health Checks" in approval_messages[0]
    assert {concept["name"] for concept in concepts["concepts"]} >= {"Route 53 Health Checks"}


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

    invalid_source = _route53_dns_record_question(None) | {"source_ref": "route53-records.md"}
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
        _invoke(
            server,
            "ingest_obsidian_note",
            {
                "path": str(_write_route53_health_note(tmp_path / "route53-health.md")),
                "subject_name": "AWS",
            },
        )
    )
    document_id = health_ingest["document"]["id"]

    with pytest.raises(Exception, match="Route 53 Health Check"):
        asyncio.run(
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


def _seed_route53_records(server: Any, tmp_path: Path) -> int:
    ingest = asyncio.run(
        _invoke(
            server,
            "ingest_obsidian_note",
            {
                "path": str(_write_route53_records_note(tmp_path / "route53-records.md")),
                "subject_name": "AWS",
            },
        )
    )
    document_id = ingest["document"]["id"]
    asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "questions": [_route53_dns_record_question(document_id)],
                "approve_new_concepts": True,
            },
        )
    )
    return document_id


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
