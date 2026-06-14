from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmcp import Context
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mcp import concept_specificity_guard, source_labels
from app.models import Quiz
from app.services import generation_service
from app.services.generation_service import GeneratedQuestionInput


@dataclass(frozen=True)
class ConceptApprovalRequest:
    subject_name: str
    questions: list[GeneratedQuestionInput]
    approve_new_concepts: bool
    save_unresolved_as_draft: bool


async def write_options_after_concept_approval(
    ctx: Context,
    db: Session,
    request: ConceptApprovalRequest,
) -> generation_service.GeneratedQuestionWriteOptions:
    if request.approve_new_concepts or request.save_unresolved_as_draft:
        return generation_service.GeneratedQuestionWriteOptions(
            approve_new_concepts=request.approve_new_concepts,
            save_unresolved_as_draft=request.save_unresolved_as_draft,
        )
    plan = generation_service.prepare_concept_plan(
        db,
        subject_name=request.subject_name,
        proposals=[
            proposal for question in request.questions for proposal in question.concept_proposals
        ],
    )
    if not plan["requires_approval"]:
        concept_specificity_guard.assert_no_specificity_bypass(
            request.questions,
            plan=plan,
            subject_name=request.subject_name,
            source_titles=source_labels.for_questions(db, request.questions),
        )
        return generation_service.GeneratedQuestionWriteOptions()
    approved = await _elicit_concept_approval(ctx, plan, question_count=len(request.questions))
    return generation_service.GeneratedQuestionWriteOptions(approve_new_concepts=approved)


def quiz_subject_name(db: Session, quiz_id: int) -> str:
    quiz = db.scalar(select(Quiz).where(Quiz.id == quiz_id))
    if quiz is None:
        raise ValueError(f"Unknown quiz id {quiz_id}")
    if quiz.primary_subject is None:
        raise ValueError("Generated questions require a quiz with a primary subject.")
    return quiz.primary_subject.name


async def _elicit_concept_approval(
    ctx: Context,
    plan: dict[str, Any],
    *,
    question_count: int,
) -> bool:
    try:
        result = await ctx.elicit(
            _concept_approval_message(plan, question_count=question_count),
            response_type=bool,
            response_title="Approve concept creation",
            response_description="Create these concepts as active taxonomy now.",
        )
    except Exception as exc:
        raise ValueError(
            "New active concepts require user approval through FastMCP elicitation. "
            "Use a client that supports elicitation, pass approve_new_concepts=true only after "
            "approval, or use save_unresolved_as_draft=true."
        ) from exc
    if result.action != "accept":
        raise ValueError("Concept creation was not approved.")
    if not result.data:
        raise ValueError("Concept creation was declined.")
    return True


def _concept_approval_message(plan: dict[str, Any], *, question_count: int) -> str:
    concept_lines = [
        _concept_approval_entry(entry) for entry in plan["entries"] if entry["resolution"] == "new"
    ]
    return "\n".join(
        [
            "Approve new active Quizzy concepts for generated questions?",
            f"Subject: {plan['subject']['name']}",
            f"Questions: {question_count}",
            "New concepts:",
            *concept_lines,
            "Accept to create these concepts as active and save active questions. "
            "Decline or cancel to leave active taxonomy unchanged.",
        ]
    )


def _concept_approval_entry(entry: dict[str, Any]) -> str:
    parent_name = entry.get("parent_name") or "top level"
    rationale = entry.get("rationale") or "No rationale provided."
    return f"- {entry['name']} ({parent_name}): {rationale}"
