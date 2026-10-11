import asyncio

import pytest

from app.mcp.server import build_server
from tests.legacy_quiz_fixtures import (
    seed_legacy_route53_concepts,
    seed_legacy_route53_duplicate_links,
)
from tests.test_mcp_integration import _invoke, route53_cleanup_questions
from tests.test_mcp_integration import mcp_database as mcp_database

pytestmark = pytest.mark.mcp


def test_mcp_can_suggest_and_merge_granular_concepts(mcp_database):
    server = build_server()

    created = seed_legacy_route53_concepts(route53_cleanup_questions())

    suggestions = asyncio.run(_invoke(server, "suggest_concept_merges", {"subject_name": "AWS"}))
    assert suggestions["one_off_concept_count"] == 3

    merge_args = {
        "subject_name": "AWS",
        "target_concept_name": "Route 53 health checks",
        "source_concept_names": ["HTTP health checks", "TCP health checks"],
    }
    dry_run = asyncio.run(_invoke(server, "merge_concepts", merge_args | {"dry_run": True}))
    assert dry_run["dry_run"] is True
    assert dry_run["affected_question_count"] == 2
    assert dry_run["merged_link_count"] == 0

    executed = asyncio.run(_invoke(server, "merge_concepts", merge_args | {"dry_run": False}))
    assert executed["dry_run"] is False
    assert executed["affected_question_count"] == 2
    assert executed["merged_link_count"] == 2

    concepts = asyncio.run(_invoke(server, "list_concepts", {"subject_name": "AWS"}))
    assert [concept["name"] for concept in concepts["concepts"]] == ["Route 53 health checks"]

    quiz = asyncio.run(_invoke(server, "get_quiz", {"quiz_id": created["id"]}))
    assert [
        [concept["name"] for concept in question["concepts"]] for question in quiz["questions"]
    ] == [
        ["Route 53 health checks"],
        ["Route 53 health checks"],
        ["Route 53 health checks"],
    ]


def test_mcp_merge_dedupes_multiple_source_links_on_same_question(mcp_database):
    server = build_server()
    created = seed_legacy_route53_duplicate_links(route53_cleanup_questions()[1])

    executed = asyncio.run(
        _invoke(
            server,
            "merge_concepts",
            {
                "subject_name": "AWS",
                "target_concept_name": "Route 53 health checks",
                "source_concept_names": ["HTTP health checks", "TCP health checks"],
                "dry_run": False,
            },
        )
    )

    assert executed["merged_link_count"] == 1
    assert executed["deduped_link_count"] == 1
    quiz = asyncio.run(_invoke(server, "get_quiz", {"quiz_id": created["id"]}))
    assert [concept["name"] for concept in quiz["questions"][0]["concepts"]] == [
        "Route 53 health checks"
    ]
