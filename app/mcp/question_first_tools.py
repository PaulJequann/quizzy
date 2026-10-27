from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field
from sqlalchemy.orm import Session

from app.models import AttemptRole, Difficulty
from app.services import generation_service, practice_service, question_health_service

RegisterTool = Callable[..., None]
SessionScope = Callable[[], AbstractContextManager[Session]]


def register_question_first_tools(
    server: FastMCP,
    register_tool: RegisterTool,
    session_scope: SessionScope,
) -> None:
    _register_practice_tools(server, register_tool, session_scope)
    _register_question_review_tools(server, register_tool, session_scope)


def _register_practice_tools(
    server: FastMCP,
    register_tool: RegisterTool,
    session_scope: SessionScope,
) -> None:
    _register_assemble_practice_session_tool(server, register_tool, session_scope)
    _register_assemble_mastery_review_tool(server, register_tool, session_scope)
    _register_assemble_concept_drill_tool(server, register_tool, session_scope)


def _register_assemble_practice_session_tool(
    server: FastMCP,
    register_tool: RegisterTool,
    session_scope: SessionScope,
) -> None:
    def assemble_practice_session(
        subject_id: Annotated[int, Field(description="Subject id to practice.")],
        count: Annotated[int, Field(description="Maximum question count.")] = 10,
        strategy: Annotated[
            str,
            Field(description="Practice assembly strategy."),
        ] = "random_subject",
        concept_ids: Annotated[
            list[int] | None,
            Field(description="Optional concepts to drill."),
        ] = None,
        difficulty: Annotated[
            Difficulty | None,
            Field(description="Optional difficulty filter."),
        ] = None,
        source_document_id: Annotated[
            int | None,
            Field(description="Optional source document filter for source_based practice."),
        ] = None,
    ) -> dict[str, Any]:
        """Assemble a disposable practice session from existing bank questions."""
        with session_scope() as db:
            quiz = practice_service.assemble_practice_session(
                db,
                practice_service.PracticeAssemblyInput(
                    subject_id=subject_id,
                    count=count,
                    strategy=strategy,
                    concept_ids=concept_ids,
                    difficulty=difficulty,
                    source_document_id=source_document_id,
                ),
            )
            return generation_service.generated_quiz_to_dict(db, quiz.id)

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(server, "assemble_practice_session", assemble_practice_session, annotations=write)


def _register_assemble_mastery_review_tool(
    server: FastMCP,
    register_tool: RegisterTool,
    session_scope: SessionScope,
) -> None:
    def assemble_mastery_review(
        subject_id: Annotated[
            int | None,
            Field(description="Optional subject id used to scope weak-concept review."),
        ] = None,
        count: Annotated[int, Field(description="Maximum question count.")] = 10,
    ) -> dict[str, Any]:
        """Assemble a disposable practice session for weak and improving concepts."""
        with session_scope() as db:
            quiz = practice_service.assemble_mastery_review(
                db,
                subject_id=subject_id,
                count=count,
            )
            return generation_service.generated_quiz_to_dict(db, quiz.id)

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(server, "assemble_mastery_review", assemble_mastery_review, annotations=write)


def _register_assemble_concept_drill_tool(
    server: FastMCP,
    register_tool: RegisterTool,
    session_scope: SessionScope,
) -> None:
    def assemble_concept_drill(
        concept_ids: Annotated[list[int], Field(description="Concept ids to drill.")],
        count: Annotated[int, Field(description="Maximum question count.")] = 10,
    ) -> dict[str, Any]:
        """Assemble a disposable practice session for specific concepts."""
        with session_scope() as db:
            quiz = practice_service.assemble_concept_drill(
                db,
                concept_ids=concept_ids,
                count=count,
            )
            return generation_service.generated_quiz_to_dict(db, quiz.id)

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(server, "assemble_concept_drill", assemble_concept_drill, annotations=write)


def _register_question_review_tools(
    server: FastMCP,
    register_tool: RegisterTool,
    session_scope: SessionScope,
) -> None:
    def list_question_review_queue(
        subject_id: Annotated[
            int | None,
            Field(description="Optional subject id used to filter open review items."),
        ] = None,
    ) -> dict[str, Any]:
        """List app-created question health review items."""
        with session_scope() as db:
            return {
                "review_items": question_health_service.list_question_review_queue(
                    db,
                    subject_id=subject_id,
                )
            }

    def prepare_question_repair_context(
        question_id: Annotated[int, Field(description="Question id to repair.")],
        actor_role: Annotated[
            AttemptRole | None,
            Field(description="Optional attempt-history filter: learner, test, or unknown."),
        ] = None,
    ) -> dict[str, Any]:
        """Read source, attempts, choices, concepts, and health evidence for one question."""
        with session_scope() as db:
            return question_health_service.prepare_question_repair_context(
                db, question_id, actor_role=actor_role
            )

    def resolve_question_review_item(
        item_id: Annotated[int, Field(description="Review item id.")],
        action: Annotated[str, Field(description="Resolution action, for example resolved.")],
        notes: Annotated[str, Field(description="Resolution notes.")] = "",
    ) -> dict[str, Any]:
        """Resolve or dismiss an app-created question review item."""
        with session_scope() as db:
            return question_health_service.resolve_question_review_item(
                db,
                item_id,
                action=action,
                notes=notes,
            )

    read = {"readOnlyHint": True, "idempotentHint": True}
    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(
        server,
        "list_question_review_queue",
        list_question_review_queue,
        annotations=read,
    )
    register_tool(
        server,
        "prepare_question_repair_context",
        prepare_question_repair_context,
        annotations=read,
    )
    register_tool(
        server,
        "resolve_question_review_item",
        resolve_question_review_item,
        annotations=write,
    )
