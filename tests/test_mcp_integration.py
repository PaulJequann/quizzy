from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.mcp.server import build_server
from app.models import Difficulty, Question, QuestionHealthState, Quiz, QuizType, SourceDocument
from app.services import (
    generation_service,
    question_health_service,
    settings_service,
    source_service,
)
from app.services.generation_service import (
    GeneratedChoiceInput,
    GeneratedQuestionInput,
    GeneratedQuizCreateInput,
)
from app.services.grading_service import submit_attempt
from tests.db_utils import clear_database_caches, reset_database

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


def write_note(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntitle: S3 Storage Classes\nsubject: AWS\n---\n"
        "# S3 Storage Classes\n\n"
        "Standard is for frequently accessed data. Glacier is for archival access.\n",
        encoding="utf-8",
    )
    return path


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
            "concept_names": ["Route 53 health checks"],
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
            "concept_names": ["HTTP health checks"],
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
            "concept_names": ["TCP health checks"],
            "source_reference": "aws/route53-health-checks.md",
        },
    ]


def test_ingest_obsidian_note_creates_or_updates_source_document(db_session: Session, tmp_path):
    note_path = write_note(tmp_path / "s3-storage.md")

    first = source_service.ingest_obsidian_note(db_session, note_path)
    second = source_service.ingest_obsidian_note(db_session, note_path, subject_name="AWS")

    documents = db_session.scalars(select(SourceDocument)).all()
    assert len(documents) == 1
    assert first.created is True
    assert second.created is False
    assert documents[0].title == "S3 Storage Classes"
    assert documents[0].subject is not None
    assert documents[0].subject.slug == "aws"
    assert "Glacier" in documents[0].content


def test_ingest_obsidian_note_uses_vault_relative_uri_and_stripped_roots(
    db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setenv("OBSIDIAN_VAULT_ROOTS", f" {tmp_path} ")
    get_settings.cache_clear()
    note_path = write_note(tmp_path / "aws" / "route53.md")

    try:
        report = source_service.ingest_obsidian_note(
            db_session,
            note_path,
            vault_name="Study Vault",
        )
    finally:
        get_settings.cache_clear()

    assert report.document.obsidian_uri == ("obsidian://open?vault=Study%20Vault&file=aws/route53")


def test_generated_quiz_uses_default_difficulty_and_source_document(db_session: Session, tmp_path):
    settings_service.set_default_difficulty(db_session, Difficulty.ADVANCED)
    note = source_service.ingest_obsidian_note(
        db_session, write_note(tmp_path / "s3.md"), subject_name="AWS"
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
        concept_names=["storage classes"],
        source_document_id=note.id,
        source_excerpt="Glacier is for archival access.",
    )

    result = generation_service.create_generated_quiz_with_questions(
        db_session,
        GeneratedQuizCreateInput(title="S3 generated quiz", subject_name="AWS"),
        questions=[question],
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
    assert saved_question.source_reference == note.path
    assert saved_question.source_document_id == note.id
    assert saved_question.generation_batch_id is not None
    assert saved_question.health_state == QuestionHealthState.UNTESTED
    assert saved_question.source_excerpt == "Glacier is for archival access."
    assert result["questions"][0]["source_excerpt"] == "Glacier is for archival access."
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
            db_session, quiz_id=quiz.id, questions=[invalid_question]
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
            quiz_id=quiz.id,
            questions=[
                GeneratedQuestionInput(
                    stem="What is S3?",
                    choices=choices,
                    source_reference="aws/s3.md",
                )
            ],
        )

    with pytest.raises(ValueError, match="at most two concepts"):
        generation_service.add_questions_to_quiz(
            db_session,
            quiz_id=quiz.id,
            questions=[
                GeneratedQuestionInput(
                    stem="What is S3?",
                    choices=choices,
                    concept_names=["S3", "object storage", "storage classes"],
                    source_reference="aws/s3.md",
                )
            ],
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
            concept_names=["Route 53 health checks"],
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
            concept_names=["Route 53 health checks"],
            source_reference="aws/route53-health-checks.md",
        ),
    ]

    result = generation_service.create_generated_quiz_with_questions(
        db_session,
        GeneratedQuizCreateInput(title="Route 53 batch", subject_name="AWS"),
        questions=questions,
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

    note_path = write_note(tmp_path / "aws-s3.md")
    ingest = asyncio.run(
        _invoke(
            server,
            "ingest_obsidian_note",
            {"path": str(note_path), "subject_name": "AWS"},
        )
    )
    document_id = ingest["document"]["id"]

    created = asyncio.run(
        _invoke(
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
                        "concept_names": ["S3 storage classes"],
                        "source_document_id": document_id,
                    }
                ],
            },
        )
    )

    assert created["title"] == "AWS S3 Generated"
    assert created["difficulty"] == "intermediate"
    assert created["quiz_type"] == "generated"
    assert created["questions"][0]["source_reference"] == str(note_path.resolve())
    assert created["questions"][0]["generation_batch_id"] is not None


def test_mcp_prepare_generation_context_returns_existing_concepts_and_policy(
    mcp_database, tmp_path
):
    server = build_server()
    note_path = write_note(tmp_path / "aws-s3.md")
    ingest = asyncio.run(
        _invoke(
            server,
            "ingest_obsidian_note",
            {"path": str(note_path), "subject_name": "AWS"},
        )
    )
    document_id = ingest["document"]["id"]

    asyncio.run(
        _invoke(
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
                        "concept_names": ["S3 storage classes"],
                        "source_document_id": document_id,
                    }
                ],
            },
        )
    )

    context = asyncio.run(
        _invoke(server, "prepare_generation_context", {"source_document_id": document_id})
    )

    assert context["subject"]["name"] == "AWS"
    assert context["source_document"]["id"] == document_id
    assert context["concept_policy"]["concepts_per_question"]["maximum"] == 2
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
        _invoke(
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
                        "concept_names": ["Route 53 health checks"],
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
                        "concept_names": ["Route 53 health checks"],
                        "source_reference": "aws/route53-health-checks.md",
                    },
                ],
            },
        )
    )

    assert result["title"] == "Route 53 batch"
    assert result["questions"][0]["concepts"] == ["Route 53 health checks"]
    assert result["questions"][1]["concepts"] == ["Route 53 health checks"]


def test_mcp_can_suggest_and_merge_granular_concepts(mcp_database):
    server = build_server()

    created = asyncio.run(
        _invoke(
            server,
            "create_generated_quiz_with_questions",
            {
                "title": "Route 53 cleanup",
                "subject_name": "AWS",
                "questions": route53_cleanup_questions(),
            },
        )
    )

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
    assert [question["concepts"] for question in quiz["questions"]] == [
        ["Route 53 health checks"],
        ["Route 53 health checks"],
        ["Route 53 health checks"],
    ]


def test_mcp_merge_dedupes_multiple_source_links_on_same_question(mcp_database):
    server = build_server()
    created = asyncio.run(
        _invoke(
            server,
            "create_generated_quiz_with_questions",
            {
                "title": "Route 53 duplicate links",
                "subject_name": "AWS",
                "questions": [
                    route53_cleanup_questions()[1]
                    | {"concept_names": ["HTTP health checks", "TCP health checks"]}
                ],
            },
        )
    )

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
    assert quiz["questions"][0]["concepts"] == ["Route 53 health checks"]


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


def create_bank_question_with_mcp(server, tmp_path) -> tuple[dict, int]:
    note_path = write_note(tmp_path / "aws-s3.md")
    ingest = asyncio.run(
        _invoke(
            server,
            "ingest_obsidian_note",
            {"path": str(note_path), "subject_name": "AWS"},
        )
    )
    document_id = ingest["document"]["id"]
    created = asyncio.run(
        _invoke(
            server,
            "create_generated_questions",
            {
                "subject_name": "AWS",
                "source_document_id": document_id,
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
                        "concept_names": ["S3 storage classes"],
                    }
                ],
            },
        )
    )
    bank = asyncio.run(_invoke(server, "list_question_bank", {"subject_name": "AWS"}))
    return created, bank["questions"][0]["subject_id"]


def assemble_mcp_practice(server, subject_id: int) -> dict:
    return asyncio.run(
        _invoke(server, "assemble_practice_session", {"subject_id": subject_id, "count": 1})
    )


def miss_practice_question(practice_id: int, question_id: int) -> None:
    with Session(get_engine()) as db:
        quiz = db.get(Quiz, practice_id)
        assert quiz is not None
        question = db.get(Question, question_id)
        assert question is not None
        wrong_choice = next(choice for choice in question.choices if not choice.is_correct)
        attempt = submit_attempt(db, quiz, {question.id: wrong_choice.id})
        question_health_service.record_attempt_evidence(db, attempt.id)


def test_mcp_question_first_generation_practice_and_repair_queue(mcp_database, tmp_path):
    server = build_server()
    created, subject_id = create_bank_question_with_mcp(server, tmp_path)
    practice = assemble_mcp_practice(server, subject_id)
    question_id = created["question_ids"][0]

    assert created["questions_created"] == 1
    assert practice["quiz_type"] == "practice"
    assert practice["questions"][0]["id"] == question_id

    miss_practice_question(practice["id"], question_id)
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
