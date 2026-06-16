from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import Client

from app.database import get_session_factory
from app.mcp.server import build_server
from app.services import generation_service
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


async def _ingest_note(client_or_server, path: Path, *, source_key: str) -> dict:
    content = path.read_text(encoding="utf-8")
    args = {"source_key": source_key, "content": content, "subject_name": "AWS"}
    if isinstance(client_or_server, Client):
        return await _client_invoke(client_or_server, "upsert_source_document_from_content", args)
    return await _invoke(client_or_server, "upsert_source_document_from_content", args)


def test_mcp_concept_plan_fails_closed_and_can_save_proposed_drafts(
    mcp_database,
    tmp_path,
):
    server = build_server()
    note_path = _write_note(tmp_path / "aws-s3.md")
    ingest = asyncio.run(_ingest_note(server, note_path, source_key="aws/aws-s3.md"))
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

    proposed = asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {"subject_name": "AWS", "questions": [question]},
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
    assert proposed["taxonomy_review"]["review_url"].endswith("/subjects/1#taxonomy-review")
    assert bank["questions"][0]["status"] == "draft"
    assert bank["questions"][0]["concepts"][0]["status"] == "proposed"
    assert active_concepts["concepts"] == []
    assert [concept["name"] for concept in review_concepts["concepts"]] == [
        "S3 storage",
        "S3 storage classes",
    ]
    assert {concept["status"] for concept in review_concepts["concepts"]} == {"proposed"}
    parent, child = review_concepts["concepts"]
    assert child["parent_concept_id"] == parent["id"]
    assert {item["suggested_action"] for item in review_queue["review_items"]} == {
        "approve_place_or_reject_concept"
    }


def test_mcp_elicitation_handler_cannot_create_active_concepts(
    mcp_database,
    tmp_path,
):
    async def fail_if_elicited(message, response_type, params, context):
        raise AssertionError("MCP concept creation should not elicit approval.")

    async def run_flow():
        server = build_server()
        async with Client(server, elicitation_handler=fail_if_elicited) as client:
            ingest = await _ingest_note(
                client,
                _write_note(tmp_path / "aws-s3.md"),
                source_key="aws/aws-s3.md",
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

    created, concepts, reviews, bank = asyncio.run(run_flow())

    assert created["questions_created"] == 1
    assert created["taxonomy_review"]["open_concept_review_count"] == 2
    assert created["taxonomy_review"]["review_path"].endswith("#taxonomy-review")
    assert concepts["concepts"] == []
    assert reviews["review_items"]
    assert bank["questions"][0]["status"] == "draft"


def test_mcp_without_elicitation_saves_new_concepts_as_draft_review(
    mcp_database,
    tmp_path,
):
    async def run_flow():
        server = build_server()
        async with Client(server) as client:
            ingest = await _ingest_note(
                client,
                _write_note(tmp_path / "aws-s3.md"),
                source_key="aws/aws-s3.md",
            )
            created = await _client_invoke(
                client,
                "create_generated_questions",
                {
                    "subject_name": "AWS",
                    "questions": [_generated_question(ingest["document"]["id"])],
                },
            )
            bank = await _client_invoke(client, "list_question_bank", {"subject_name": "AWS"})
            return created, bank

    created, bank = asyncio.run(run_flow())

    assert created["questions_created"] == 1
    assert created["taxonomy_review"]["review_url"].endswith("/subjects/1#taxonomy-review")
    assert bank["questions"][0]["status"] == "draft"


def test_mcp_routes_broad_concept_bypass_to_draft_review(
    mcp_database,
    tmp_path,
):
    server = build_server()
    note_path = _write_route53_health_note(tmp_path / "route53-health.md")
    ingest = asyncio.run(_ingest_note(server, note_path, source_key="aws/route53-health.md"))
    document_id = ingest["document"]["id"]

    seeded = asyncio.run(_approved_create(server, [_route53_dns_record_question(document_id)]))
    assert seeded["questions_created"] == 1

    created = asyncio.run(
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
    reviews = asyncio.run(
        _invoke(
            server,
            "list_concept_review_queue",
            {"subject_id": ingest["document"]["subject_id"]},
        )
    )
    assert created["questions_created"] == 3
    assert [question["status"] for question in bank["questions"]].count("draft") == 3
    assert reviews["review_items"][0]["review_type"] == "specificity_mismatch"


def test_mcp_parentless_new_concept_requires_placement_review(
    mcp_database,
    tmp_path,
):
    server = build_server()
    note_path = _write_route53_routing_note(tmp_path / "routing-policies.md")
    ingest = asyncio.run(_ingest_note(server, note_path, source_key="aws/routing-policies.md"))

    created = asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "questions": [_parentless_route53_question(ingest["document"]["id"])],
            },
        )
    )
    concepts = asyncio.run(
        _invoke(
            server,
            "list_concepts",
            {"subject_name": "AWS", "include_proposed": True},
        )
    )
    reviews = asyncio.run(
        _invoke(
            server,
            "list_concept_review_queue",
            {"subject_id": ingest["document"]["subject_id"]},
        )
    )

    assert created["questions_created"] == 1
    assert created["taxonomy_review"]["review_url"].endswith("/subjects/1#taxonomy-review")
    assert concepts["concepts"][0]["name"] == "Route 53 Routing Policy Selection"
    assert concepts["concepts"][0]["status"] == "proposed"
    assert concepts["concepts"][0]["parent_concept_id"] is None
    assert reviews["review_items"][0]["review_type"] == "proposed_concept_placement"
    assert reviews["review_items"][0]["suggested_action"] == "approve_place_or_reject_concept"


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


async def _approved_create(server, questions: list[dict]) -> dict:
    session_factory = get_session_factory()
    with session_factory() as db:
        return generation_service.create_generated_questions(
            db,
            generation_service.QuestionBankCreateInput(
                subject_name="AWS",
                questions=[
                    generation_service.GeneratedQuestionInput.model_validate(question)
                    for question in questions
                ],
                approve_new_concepts=True,
            ),
        )


def _write_route53_health_note(path: Path) -> Path:
    path.write_text(
        "---\ntitle: Route 53 Health Checks\nsubject: AWS\n---\n"
        "# Route 53 Health Checks\n\n"
        "Route 53 health checks evaluate endpoint health for failover records.\n",
        encoding="utf-8",
    )
    return path


def _write_route53_routing_note(path: Path) -> Path:
    path.write_text(
        "---\ntitle: Route 53 Routing Policies\nsubject: AWS\n---\n"
        "# Route 53 Routing Policies\n\n"
        "Weighted, latency, failover, geolocation, and geoproximity policies choose DNS "
        "answers for different traffic-management goals.\n",
        encoding="utf-8",
    )
    return path


def _parentless_route53_question(document_id: int) -> dict:
    return {
        "stem": "Which Route 53 policy best routes users to the Region with the lowest latency?",
        "choices": [
            {"text": "Latency routing policy", "is_correct": True},
            {"text": "Weighted routing policy"},
            {"text": "Simple routing policy"},
            {"text": "Multivalue answer routing policy"},
        ],
        "explanation": "Latency routing selects the lowest-latency Region from Route 53 data.",
        "concept_proposals": [
            {
                "name": "Route 53 Routing Policy Selection",
                "role": "primary",
                "rationale": "Choosing a Route 53 routing policy is a reusable objective.",
            }
        ],
        "source_document_id": document_id,
    }


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
