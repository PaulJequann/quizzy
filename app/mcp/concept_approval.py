from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    save_unresolved_as_draft: bool


async def write_options_after_concept_approval(
    db: Session,
    request: ConceptApprovalRequest,
) -> generation_service.GeneratedQuestionWriteOptions:
    plan = generation_service.prepare_concept_plan(
        db,
        subject_name=request.subject_name,
        proposals=[
            proposal for question in request.questions for proposal in question.concept_proposals
        ],
    )
    if request.save_unresolved_as_draft:
        return _draft_options("Agent requested draft/proposed taxonomy review.", plan=plan)
    if not plan["requires_approval"]:
        topic = concept_specificity_guard.uncovered_source_topic(
            request.questions,
            plan=plan,
            subject_name=request.subject_name,
            source_titles=source_labels.for_questions(db, request.questions),
        )
        if topic:
            return _specificity_draft_options(topic, plan=plan)
        return generation_service.GeneratedQuestionWriteOptions()
    return _draft_options(
        (
            "New concepts need stewardship review before becoming active. Review parent "
            "placement before approval; agents should propose parent_name for source-specific "
            "concepts when a durable parent exists."
        ),
        plan=plan,
        suggested_action="approve_place_or_reject_concept",
    )


def _draft_options(
    reason: str,
    *,
    plan: dict[str, Any],
    suggested_action: str = "approve_or_reject_concept",
) -> generation_service.GeneratedQuestionWriteOptions:
    return generation_service.GeneratedQuestionWriteOptions(
        save_unresolved_as_draft=True,
        draft_review_reason=reason,
        draft_suggested_action=suggested_action,
        draft_review_evidence={"concept_plan": plan},
    )


def _specificity_draft_options(
    topic: str,
    *,
    plan: dict[str, Any],
) -> generation_service.GeneratedQuestionWriteOptions:
    return generation_service.GeneratedQuestionWriteOptions(
        save_unresolved_as_draft=True,
        draft_review_type="specificity_mismatch",
        draft_review_reason=(
            f"Question appears to need a more specific concept for '{topic}' before it can "
            "become active."
        ),
        draft_suggested_action="approve_new_concept_or_map_existing",
        draft_review_evidence={"candidate_topic": topic, "concept_plan": plan},
    )


def quiz_subject_name(db: Session, quiz_id: int) -> str:
    quiz = db.scalar(select(Quiz).where(Quiz.id == quiz_id))
    if quiz is None:
        raise ValueError(f"Unknown quiz id {quiz_id}")
    if quiz.primary_subject is None:
        raise ValueError("Generated questions require a quiz with a primary subject.")
    return quiz.primary_subject.name
