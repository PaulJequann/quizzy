from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

from app.mcp.server import build_server
from app.services.concept_service import normalize_concept_name
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


def _write_note(path: Path) -> Path:
    path.write_text(
        "---\ntitle: S3 Storage Classes\nsubject: AWS\n---\n"
        "# S3 Storage Classes\n\n"
        "Standard is for frequently accessed data. Glacier is for archival access.\n",
        encoding="utf-8",
    )
    return path


def test_mcp_concept_plan_fails_closed_and_can_save_proposed_drafts(
    mcp_database,
    tmp_path,
):
    server = build_server()
    note_path = _write_note(tmp_path / "aws-s3.md")
    ingest = asyncio.run(
        _invoke(
            server,
            "ingest_obsidian_note",
            {"path": str(note_path), "subject_name": "AWS"},
        )
    )
    question = _generated_question(ingest["document"]["id"])

    plan = asyncio.run(
        _invoke(
            server,
            "prepare_concept_plan",
            {"subject_name": "AWS", "proposals": question["concept_proposals"]},
        )
    )
    assert plan["requires_approval"] is True
    assert plan["entries"][0]["parent"]["resolution"] == "new"

    with pytest.raises(ToolError, match="FastMCP elicitation"):
        asyncio.run(
            _invoke(
                server,
                "create_generated_questions",
                {"subject_name": "AWS", "questions": [question]},
            )
        )

    proposed = asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "questions": [question],
                "save_unresolved_as_draft": True,
            },
        )
    )
    bank = asyncio.run(_invoke(server, "list_question_bank", {"subject_name": "AWS"}))
    active_concepts = asyncio.run(_invoke(server, "list_concepts", {"subject_name": "AWS"}))
    review_concepts = asyncio.run(
        _invoke(
            server,
            "list_concepts",
            {"subject_name": "AWS", "include_proposed": True},
        )
    )
    review_queue = asyncio.run(
        _invoke(
            server,
            "list_concept_review_queue",
            {"subject_id": ingest["document"]["subject_id"]},
        )
    )

    assert proposed["questions_created"] == 1
    assert bank["questions"][0]["status"] == "draft"
    assert bank["questions"][0]["concepts"][0]["status"] == "proposed"
    assert active_concepts["concepts"] == []
    assert [concept["status"] for concept in review_concepts["concepts"]] == ["proposed"]
    assert review_queue["review_items"][0]["suggested_action"] == "approve_or_reject_concept"


def test_mcp_elicitation_approval_creates_active_concepts(
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
                {"path": str(_write_note(tmp_path / "aws-s3.md")), "subject_name": "AWS"},
            )
            created = await _client_invoke(
                client,
                "create_generated_questions",
                {
                    "subject_name": "AWS",
                    "questions": [_generated_question(ingest["document"]["id"])],
                },
            )
            concepts = await _client_invoke(
                client,
                "list_concepts",
                {"subject_name": "AWS"},
            )
            reviews = await _client_invoke(
                client,
                "list_concept_review_queue",
                {"subject_id": ingest["document"]["subject_id"]},
            )
            bank = await _client_invoke(client, "list_question_bank", {"subject_name": "AWS"})
            return created, concepts, reviews, bank

    approval_messages: list[str] = []
    created, concepts, reviews, bank = asyncio.run(run_flow())

    assert created["questions_created"] == 1
    assert approval_messages
    assert "Approve new active Quizzy concepts" in approval_messages[0]
    assert concepts["concepts"][0]["status"] == "active"
    assert reviews["review_items"] == []
    assert bank["questions"][0]["status"] == "active"


def test_mcp_elicitation_decline_does_not_create_questions(
    mcp_database,
    tmp_path,
):
    async def decline_concepts(message, response_type, params, context):
        return ElicitResult(action="decline")

    async def run_flow():
        server = build_server()
        async with Client(server, elicitation_handler=decline_concepts) as client:
            ingest = await _client_invoke(
                client,
                "ingest_obsidian_note",
                {"path": str(_write_note(tmp_path / "aws-s3.md")), "subject_name": "AWS"},
            )
            with pytest.raises(ToolError, match="Concept creation was not approved"):
                await _client_invoke(
                    client,
                    "create_generated_questions",
                    {
                        "subject_name": "AWS",
                        "questions": [_generated_question(ingest["document"]["id"])],
                    },
                )
            return await _client_invoke(client, "get_status", {})

    status = asyncio.run(run_flow())

    assert status["questions"] == 0


def test_mcp_rejects_broad_concept_bypass_for_repeated_specific_topic(
    mcp_database,
    tmp_path,
):
    server = build_server()
    note_path = _write_route53_health_note(tmp_path / "route53-health.md")
    ingest = asyncio.run(
        _invoke(
            server,
            "ingest_obsidian_note",
            {"path": str(note_path), "subject_name": "AWS"},
        )
    )
    document_id = ingest["document"]["id"]

    seeded = asyncio.run(
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
    assert seeded["questions_created"] == 1

    with pytest.raises(ToolError, match="more specific concept"):
        asyncio.run(
            _invoke(
                server,
                "create_generated_questions",
                {
                    "subject_name": "AWS",
                    "questions": [_health_check_question(document_id, index) for index in range(3)],
                },
            )
        )

    bank = asyncio.run(_invoke(server, "list_question_bank", {"subject_name": "AWS"}))
    assert len(bank["questions"]) == 1


def test_alias_normalization_preserves_alias_token():
    assert (
        normalize_concept_name("Route 53 Alias Records", subject_name="AWS")
        == "route-53-alias-record"
    )


def _generated_question(document_id: int) -> dict:
    return {
        "stem": "Which S3 storage class is intended for archival data?",
        "choices": [
            {"text": "S3 Glacier", "is_correct": True},
            {"text": "S3 Standard because it is always cheapest"},
            {"text": "CloudFront because it stores all origin data permanently"},
            {"text": "IAM because policies retain objects"},
        ],
        "concept_proposals": [
            {
                "name": "S3 storage classes",
                "parent_name": "S3 storage",
                "role": "primary",
                "rationale": "Reusable storage-class objective under the S3 storage map.",
            }
        ],
        "source_document_id": document_id,
    }


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


def _health_check_question(document_id: int, index: int) -> dict:
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
                "name": "Route 53 DNS Records",
                "role": "primary",
                "rationale": (
                    "Tests Route 53 health check behavior but keeps the broad record concept."
                ),
            }
        ],
        "source_document_id": document_id,
    }
