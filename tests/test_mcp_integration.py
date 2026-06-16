from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory
from app.mcp.server import build_server
from app.models import (
    ConceptAssignmentRole,
    Difficulty,
    Question,
    QuestionHealthState,
    Quiz,
    QuizType,
)
from app.services import (
    generation_service,
    settings_service,
    source_service,
)
from app.services.generation_service import (
    GeneratedChoiceInput,
    GeneratedConceptProposal,
    GeneratedQuestionInput,
    GeneratedQuizCreateInput,
)
from tests.db_utils import clear_database_caches, reset_database
from tests.legacy_quiz_fixtures import (
    seed_legacy_route53_concepts,
    seed_legacy_route53_duplicate_links,
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


async def _invoke(server, name: str, args: dict):
    result = await server.call_tool(name, args)
    return result.structured_content


async def _client_invoke(client, name: str, args: dict):
    result = await client.call_tool(name, args)
    return result.data


async def _approved_invoke(server, name: str, args: dict):
    async with Client(server) as client:
        return await _client_invoke(client, name, args)


def write_note(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntitle: S3 Storage Classes\nsubject: AWS\n---\n"
        "# S3 Storage Classes\n\n"
        "Standard is for frequently accessed data. Glacier is for archival access.\n",
        encoding="utf-8",
    )
    return path


def _ingest_note_content(
    db: Session,
    note_path: Path,
    source_key: str,
):
    return source_service.upsert_source_document_from_content(
        db,
        source_service.SourceDocumentContentInput(
            source_key=source_key,
            display_path=source_key,
            content=note_path.read_text(encoding="utf-8"),
            subject_name="AWS",
        ),
    )


def _mcp_ingest_note_content(
    server,
    note_path: Path,
    *,
    source_key: str,
    subject_name: str = "AWS",
):
    return asyncio.run(
        _invoke(
            server,
            "upsert_source_document_from_content",
            {
                "source_key": source_key,
                "content": note_path.read_text(encoding="utf-8"),
                "subject_name": subject_name,
            },
        )
    )


def route53_cleanup_questions() -> list[dict]:
    return [
        {
            "stem": "What does a Route 53 health check influence?",
            "choices": [
                {"text": "Whether a DNS record stays in answers", "is_correct": True},
                {"text": "Whether IAM allows a principal"},
                {"text": "Whether S3 encrypts a bucket"},
                {"text": "Whether EC2 changes instance family"},
            ],
            "concept_proposals": [
                {
                    "name": "Route 53 health checks",
                    "role": "primary",
                    "rationale": "Core reusable Route 53 health-check objective.",
                }
            ],
            "source_reference": "aws/route53-health-checks.md",
        },
        {
            "stem": "Which protocol can an endpoint health check use?",
            "choices": [
                {"text": "HTTP", "is_correct": True},
                {"text": "IAM"},
                {"text": "KMS"},
                {"text": "SQS"},
            ],
            "concept_proposals": [
                {
                    "name": "HTTP health checks",
                    "role": "primary",
                    "rationale": "Protocol-specific proposal used by merge tests.",
                }
            ],
            "source_reference": "aws/route53-health-checks.md",
        },
        {
            "stem": "Which non-HTTP protocol can a Route 53 health check use?",
            "choices": [
                {"text": "TCP", "is_correct": True},
                {"text": "SAML"},
                {"text": "S3"},
                {"text": "STS"},
            ],
            "concept_proposals": [
                {
                    "name": "TCP health checks",
                    "role": "primary",
                    "rationale": "Protocol-specific proposal used by merge tests.",
                }
            ],
            "source_reference": "aws/route53-health-checks.md",
        },
    ]


def _s3_storage_question(document_id: int) -> dict:
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
        "source_document_id": document_id,
    }


def _seed_active_generated_questions(questions: list[dict]) -> dict:
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


def test_generated_quiz_uses_default_difficulty_and_source_document(db_session: Session, tmp_path):
    settings_service.set_default_difficulty(db_session, Difficulty.ADVANCED)
    note = _ingest_note_content(
        db_session, write_note(tmp_path / "s3.md"), source_key="aws/s3.md"
    ).document
    question = GeneratedQuestionInput(
        stem="Which S3 storage class is best aligned with archival access?",
        choices=[
            GeneratedChoiceInput(text="S3 Glacier", is_correct=True),
            GeneratedChoiceInput(text="S3 Standard for all archival data"),
            GeneratedChoiceInput(text="EBS snapshots as an S3 class"),
            GeneratedChoiceInput(text="CloudFront edge cache"),
        ],
        explanation="Glacier is designed for archival storage access patterns.",
        concept_proposals=[
            GeneratedConceptProposal(
                name="storage classes",
                role=ConceptAssignmentRole.PRIMARY,
                rationale="Reusable S3 storage-class learning objective.",
            )
        ],
        source_document_id=note.id,
        source_excerpt="Glacier is for archival access.",
    )

    result = generation_service.create_generated_quiz_with_questions(
        db_session,
        GeneratedQuizCreateInput(title="S3 generated quiz", subject_name="AWS"),
        questions=[question],
        options=generation_service.GeneratedQuestionWriteOptions(approve_new_concepts=True),
    )

    quiz = db_session.scalar(select(Quiz).where(Quiz.id == result["id"]))
    saved_question = db_session.scalar(select(Question))
    assert quiz is not None
    assert quiz.quiz_type == QuizType.GENERATED
    assert quiz.difficulty == Difficulty.ADVANCED
    assert saved_question is not None
    source_reference_type = Question.__table__.c.source_reference.type
    assert isinstance(source_reference_type, String)
    assert source_reference_type.length == 1024
    assert saved_question.source_reference == note.source_key
    assert saved_question.source_document_id == note.id
    assert saved_question.source_snapshot_id == note.current_snapshot_id
    assert saved_question.generation_batch_id is not None
    assert saved_question.health_state == QuestionHealthState.UNTESTED
    assert saved_question.source_excerpt == "Glacier is for archival access."
    assert result["questions"][0]["source_excerpt"] == "Glacier is for archival access."
    assert result["questions"][0]["source_snapshot_id"] == note.current_snapshot_id
    assert [concept.name for concept in saved_question.concepts] == ["storage classes"]


def test_default_difficulty_falls_back_to_environment(db_session: Session, monkeypatch):
    monkeypatch.setenv("DEFAULT_DIFFICULTY", "advanced")
    get_settings.cache_clear()

    try:
        assert settings_service.get_default_difficulty(db_session) == Difficulty.ADVANCED
    finally:
        get_settings.cache_clear()


def test_generated_questions_require_one_correct_choice_and_source(db_session: Session):
    quiz = generation_service.create_generated_quiz(
        db_session, GeneratedQuizCreateInput(title="Generated", subject_name="AWS")
    )
    invalid_question = GeneratedQuestionInput(
        stem="What is S3?",
        choices=[
            GeneratedChoiceInput(text="Object storage"),
            GeneratedChoiceInput(text="Compute"),
            GeneratedChoiceInput(text="Queue"),
            GeneratedChoiceInput(text="DNS"),
        ],
        source_reference="aws/s3.md",
    )

    with pytest.raises(ValueError, match="exactly one correct"):
        generation_service.add_questions_to_quiz(
            db_session,
            generation_service.AddQuestionsToQuizInput(
                quiz_id=quiz.id,
                questions=[invalid_question],
            ),
        )


def test_create_generated_quiz_with_questions_validates_before_creating_quiz(
    db_session: Session,
):
    invalid_question = GeneratedQuestionInput(
        stem="What is S3?",
        choices=[
            GeneratedChoiceInput(text="Object storage", is_correct=True),
            GeneratedChoiceInput(text="Compute"),
            GeneratedChoiceInput(text="Queue"),
            GeneratedChoiceInput(text="DNS"),
        ],
    )

    with pytest.raises(ValueError, match="source_document_id or source_reference"):
        generation_service.create_generated_quiz_with_questions(
            db_session,
            GeneratedQuizCreateInput(title="Should not persist", subject_name="AWS"),
            questions=[invalid_question],
        )

    assert db_session.scalars(select(Quiz)).all() == []


def test_generated_questions_require_one_or_two_concepts(db_session: Session):
    quiz = generation_service.create_generated_quiz(
        db_session, GeneratedQuizCreateInput(title="Generated", subject_name="AWS")
    )
    choices = [
        GeneratedChoiceInput(text="Object storage", is_correct=True),
        GeneratedChoiceInput(text="Compute"),
        GeneratedChoiceInput(text="Queue"),
        GeneratedChoiceInput(text="DNS"),
    ]

    with pytest.raises(ValueError, match="at least one concept"):
        generation_service.add_questions_to_quiz(
            db_session,
            generation_service.AddQuestionsToQuizInput(
                quiz_id=quiz.id,
                questions=[
                    GeneratedQuestionInput(
                        stem="What is S3?",
                        choices=choices,
                        source_reference="aws/s3.md",
                    )
                ],
            ),
        )

    with pytest.raises(ValueError, match="at most two concepts"):
        generation_service.add_questions_to_quiz(
            db_session,
            generation_service.AddQuestionsToQuizInput(
                quiz_id=quiz.id,
                questions=[
                    GeneratedQuestionInput(
                        stem="What is S3?",
                        choices=choices,
                        concept_proposals=[
                            GeneratedConceptProposal(
                                name="S3",
                                role=ConceptAssignmentRole.PRIMARY,
                            ),
                            GeneratedConceptProposal(
                                name="object storage",
                                role=ConceptAssignmentRole.SECONDARY,
                            ),
                            GeneratedConceptProposal(
                                name="storage classes",
                                role=ConceptAssignmentRole.SECONDARY,
                            ),
                        ],
                        source_reference="aws/s3.md",
                    )
                ],
            ),
        )


def test_generated_quiz_reuses_concepts_within_one_batch(db_session: Session):
    questions = [
        GeneratedQuestionInput(
            stem="What does a Route 53 health check influence?",
            choices=[
                GeneratedChoiceInput(text="Whether a DNS record stays in answers", is_correct=True),
                GeneratedChoiceInput(text="Whether IAM allows a principal"),
                GeneratedChoiceInput(text="Whether S3 encrypts a bucket"),
                GeneratedChoiceInput(text="Whether EC2 changes instance family"),
            ],
            concept_proposals=[
                GeneratedConceptProposal(
                    name="Route 53 health checks",
                    role=ConceptAssignmentRole.PRIMARY,
                    rationale="Reusable Route 53 health-check learning objective.",
                )
            ],
            source_reference="aws/route53-health-checks.md",
        ),
        GeneratedQuestionInput(
            stem="What can Route 53 stop returning when a health check is unhealthy?",
            choices=[
                GeneratedChoiceInput(text="The associated DNS record", is_correct=True),
                GeneratedChoiceInput(text="The hosted zone itself"),
                GeneratedChoiceInput(text="Every alias record in the account"),
                GeneratedChoiceInput(text="CloudWatch alarm history"),
            ],
            concept_proposals=[
                GeneratedConceptProposal(
                    name="Route 53 health checks",
                    role=ConceptAssignmentRole.PRIMARY,
                    rationale="Same batch should reuse the approved concept.",
                )
            ],
            source_reference="aws/route53-health-checks.md",
        ),
    ]

    result = generation_service.create_generated_quiz_with_questions(
        db_session,
        GeneratedQuizCreateInput(title="Route 53 batch", subject_name="AWS"),
        questions=questions,
        options=generation_service.GeneratedQuestionWriteOptions(approve_new_concepts=True),
    )

    saved_question_count = db_session.scalar(select(func.count(Question.id)))
    assert result["title"] == "Route 53 batch"
    assert saved_question_count == 2


def test_mcp_guidance_and_generation_workflow(mcp_database, tmp_path):
    server = build_server()
    guidance = asyncio.run(_invoke(server, "get_generation_guidance", {}))
    assert "Distractor Control" in guidance["guidance"]
    assert "common misconceptions" in guidance["guidance"]
    assert "create_generated_questions" in guidance["guidance"]
    assert "Concept granularity rules" in guidance["guidance"]
    assert "canonical concept plan" in guidance["guidance"]
    assert "upsert_source_document_from_content" in guidance["guidance"]

    note_path = write_note(tmp_path / "aws-s3.md")
    ingest = _mcp_ingest_note_content(server, note_path, source_key="aws/aws-s3.md")
    document_id = ingest["document"]["id"]

    created = asyncio.run(
        _approved_invoke(
            server,
            "create_generated_quiz_with_questions",
            {
                "title": "AWS S3 Generated",
                "subject_name": "AWS",
                "questions": [
                    {
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
                        "source_document_id": document_id,
                    }
                ],
            },
        )
    )

    assert created["title"] == "AWS S3 Generated"
    assert created["difficulty"] == "intermediate"
    assert created["quiz_type"] == "generated"
    assert created["questions"][0]["source_reference"] == "aws/aws-s3.md"
    assert created["questions"][0]["source_snapshot_id"] == ingest["source_snapshot_id"]
    assert created["questions"][0]["generation_batch_id"] is not None
    assert created["questions"][0]["status"] == "draft"
    assert created["taxonomy_review"]["review_url"].endswith("/subjects/1#taxonomy-review")


def test_mcp_prepare_generation_context_returns_existing_concepts_and_policy(
    mcp_database, tmp_path
):
    server = build_server()
    note_path = write_note(tmp_path / "aws-s3.md")
    ingest = _mcp_ingest_note_content(server, note_path, source_key="aws/aws-s3.md")
    document_id = ingest["document"]["id"]

    _seed_active_generated_questions([_s3_storage_question(document_id)])

    context = asyncio.run(
        _invoke(server, "prepare_generation_context", {"source_document_id": document_id})
    )

    assert context["subject"]["name"] == "AWS"
    assert context["source_document"]["id"] == document_id
    assert context["concept_policy"]["concepts_per_question"]["maximum"] == 2
    assert context["concept_policy"]["concept_planning_required"] is True
    assert context["concept_policy"]["reuse_first"] is True
    assert [concept["name"] for concept in context["existing_concepts"]] == ["S3 storage classes"]


def test_mcp_default_difficulty_can_change_without_server_restart(mcp_database):
    server = build_server()
    initial = asyncio.run(_invoke(server, "get_status", {}))
    assert initial["default_difficulty"] == "intermediate"

    updated = asyncio.run(_invoke(server, "set_default_difficulty", {"difficulty": "advanced"}))
    assert updated["default_difficulty"] == "advanced"

    status = asyncio.run(_invoke(server, "get_status", {}))
    assert status["default_difficulty"] == "advanced"

    quiz = asyncio.run(
        _invoke(
            server, "create_generated_quiz", {"title": "Dynamic default", "subject_name": "AWS"}
        )
    )
    assert quiz["difficulty"] == "advanced"


def test_mcp_generated_quiz_reuses_concepts_within_one_batch(mcp_database):
    server = build_server()

    result = asyncio.run(
        _approved_invoke(
            server,
            "create_generated_quiz_with_questions",
            {
                "title": "Route 53 batch",
                "subject_name": "AWS",
                "questions": [
                    {
                        "stem": "What does a Route 53 health check influence?",
                        "choices": [
                            {
                                "text": "Whether a DNS record stays in answers",
                                "is_correct": True,
                            },
                            {"text": "Whether IAM allows a principal"},
                            {"text": "Whether S3 encrypts a bucket"},
                            {"text": "Whether EC2 changes instance family"},
                        ],
                        "concept_proposals": [
                            {
                                "name": "Route 53 health checks",
                                "role": "primary",
                                "rationale": "Reusable Route 53 health-check learning objective.",
                            }
                        ],
                        "source_reference": "aws/route53-health-checks.md",
                    },
                    {
                        "stem": (
                            "What can Route 53 stop returning when a health check is unhealthy?"
                        ),
                        "choices": [
                            {"text": "The associated DNS record", "is_correct": True},
                            {"text": "The hosted zone itself"},
                            {"text": "Every alias record in the account"},
                            {"text": "CloudWatch alarm history"},
                        ],
                        "concept_proposals": [
                            {
                                "name": "Route 53 health checks",
                                "role": "primary",
                                "rationale": "Same batch should reuse the approved concept.",
                            }
                        ],
                        "source_reference": "aws/route53-health-checks.md",
                    },
                ],
            },
        )
    )

    assert result["title"] == "Route 53 batch"
    assert result["questions"][0]["status"] == "draft"
    assert result["questions"][1]["status"] == "draft"
    assert result["taxonomy_review"]["open_concept_review_count"] == 2
    assert [concept["name"] for concept in result["questions"][0]["concepts"]] == [
        "Route 53 health checks"
    ]
    assert [concept["name"] for concept in result["questions"][1]["concepts"]] == [
        "Route 53 health checks"
    ]


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


def test_mcp_rejects_unsourced_generated_question(mcp_database):
    server = build_server()
    quiz = asyncio.run(
        _invoke(server, "create_generated_quiz", {"title": "Draft", "subject_name": "AWS"})
    )

    with pytest.raises(ToolError, match="source_document_id or source_reference"):
        asyncio.run(
            _invoke(
                server,
                "add_questions_to_quiz",
                {
                    "quiz_id": quiz["id"],
                    "questions": [
                        {
                            "stem": "What is S3?",
                            "choices": [
                                {"text": "Object storage", "is_correct": True},
                                {"text": "Compute"},
                                {"text": "Queue"},
                                {"text": "DNS"},
                            ],
                        }
                    ],
                },
            )
        )
