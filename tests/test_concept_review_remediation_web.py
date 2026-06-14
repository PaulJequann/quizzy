import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Choice,
    Concept,
    ConceptAssignmentRole,
    ConceptReviewItem,
    ConceptStatus,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionStatus,
)
from app.services.concept_service import normalize_concept_name

pytestmark = pytest.mark.api


def test_subject_review_accepts_current_assignment(
    client: TestClient,
    db_session: Session,
) -> None:
    source = _existing_concept(db_session)
    question, review = _draft_specificity_review(
        source,
        stem="Which pytest command discovers tests?",
        correct_choice="pytest",
    )
    db_session.add(review)
    db_session.commit()

    detail_response = client.get(f"/subjects/{source.subject_id}")
    assert detail_response.status_code == 200
    assert "Keep assignment" in detail_response.text
    assert "Move question" in detail_response.text

    response = client.post(
        f"/concept-reviews/{review.id}/accept-assignment",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/subjects/{source.subject_id}"
    db_session.refresh(question)
    db_session.refresh(review)
    assert question.status == QuestionStatus.ACTIVE
    assert review.status == "resolved"


def test_subject_review_remaps_draft_question_to_existing_concept(
    client: TestClient,
    db_session: Session,
) -> None:
    source = _existing_concept(db_session)
    target = _target_concept(source)
    question, review = _draft_specificity_review(
        source,
        stem="Which fixture scope is reused for every test function?",
        correct_choice="function",
    )
    db_session.add_all([target, review])
    db_session.commit()

    response = client.post(
        f"/concept-reviews/{review.id}/remap",
        data={"target_concept_id": str(target.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/subjects/{source.subject_id}"
    db_session.refresh(question)
    db_session.refresh(review)
    assert question.status == QuestionStatus.ACTIVE
    assert review.status == "resolved"
    assert [link.concept.name for link in question.concept_links] == ["fixture scopes"]


def _existing_concept(db_session: Session) -> Concept:
    concept = db_session.scalar(select(Concept).where(Concept.name == "pytest basics"))
    assert concept is not None
    return concept


def _target_concept(source: Concept) -> Concept:
    return Concept(
        subject=source.subject,
        name="fixture scopes",
        slug="fixture-scopes",
        normalized_name=normalize_concept_name(
            "fixture scopes",
            subject_name=source.subject.name,
        ),
        status=ConceptStatus.ACTIVE,
    )


def _draft_specificity_review(
    concept: Concept,
    *,
    stem: str,
    correct_choice: str,
) -> tuple[Question, ConceptReviewItem]:
    question = Question(
        subject=concept.subject,
        stem=stem,
        status=QuestionStatus.DRAFT,
        difficulty=Difficulty.BEGINNER,
        source_reference="testing/pytest.md#remediation",
    )
    question.choices = [
        Choice(text=correct_choice, is_correct=True, order_index=1),
        Choice(text="docker", is_correct=False, order_index=2),
        Choice(text="alembic", is_correct=False, order_index=3),
        Choice(text="uvicorn", is_correct=False, order_index=4),
    ]
    question.concept_links = [QuestionConcept(concept=concept, role=ConceptAssignmentRole.PRIMARY)]
    return question, ConceptReviewItem(
        subject=concept.subject,
        concept=concept,
        question=question,
        review_type="specificity_mismatch",
        reason="The source appears more specific than the selected concept.",
        suggested_action="approve_new_concept_or_map_existing",
        evidence_json="{}",
    )
