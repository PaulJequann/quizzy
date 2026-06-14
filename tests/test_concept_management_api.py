import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Concept,
    ConceptDecisionEvent,
    ConceptReviewItem,
    ConceptStatus,
    Question,
    QuestionConcept,
    Subject,
)
from app.services.concept_service import normalize_concept_name

pytestmark = pytest.mark.api


def test_concept_management_api_creates_user_managed_concepts(
    client: TestClient,
    db_session: Session,
) -> None:
    parent = _add_concept(db_session, "pytest")

    response = client.post(
        f"/api/subjects/{parent.subject_id}/concepts",
        json={
            "name": "pytest fixtures",
            "parent_concept_id": parent.id,
            "rationale": "Create a manually managed taxonomy node.",
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "pytest fixtures"
    assert response.json()["status"] == "active"
    assert response.json()["parent_concept_id"] == parent.id
    assert _concept(db_session, "pytest fixtures").parent_concept_id == parent.id


def test_concept_management_api_renames_moves_and_archives(
    client: TestClient,
    db_session: Session,
) -> None:
    concept = _concept(db_session, "pytest basics")
    parent = _add_concept(db_session, "pytest")

    response = client.patch(
        f"/api/concepts/{concept.id}",
        json={
            "name": "pytest fundamentals",
            "parent_concept_id": parent.id,
            "rationale": "Clean taxonomy naming.",
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "pytest fundamentals"
    assert response.json()["parent_concept_id"] == parent.id

    archive_response = client.post(
        f"/api/concepts/{concept.id}/archive",
        json={"archive_children": False, "rationale": "No longer current."},
    )

    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"


def test_concept_management_api_splits_and_merges(
    client: TestClient,
    db_session: Session,
) -> None:
    source = _concept(db_session, "pytest basics")
    target = _add_concept(db_session, "testing fundamentals")
    unrelated = _add_concept(db_session, "pending draft concept")
    question = db_session.scalar(select(Question).where(Question.stem == "What does pytest run?"))
    assert question is not None
    review = ConceptReviewItem(
        subject=source.subject,
        concept=unrelated,
        review_type="unresolved_assignment",
        reason="Unrelated draft question needs stewardship.",
        suggested_action="approve_or_reject_concept",
        evidence_json="{}",
    )
    db_session.add(review)
    db_session.commit()

    split_response = client.post(
        f"/api/concepts/{source.id}/split",
        json={
            "child_names": ["pytest collection"],
            "question_ids_by_child_name": {"pytest collection": [question.id]},
            "rationale": "Separate collection behavior.",
        },
    )

    assert split_response.status_code == 200
    child = _concept(db_session, "pytest collection")
    assert child.parent_concept_id == source.id

    merge_response = client.post(
        "/api/concepts/merge",
        json={
            "target_concept_id": target.id,
            "source_concept_ids": [child.id],
            "dry_run": False,
        },
    )

    assert merge_response.status_code == 200
    assert merge_response.json()["merged_link_count"] == 1
    db_session.refresh(child)
    assert child.status == ConceptStatus.ARCHIVED
    assert db_session.get(
        QuestionConcept,
        {"question_id": question.id, "concept_id": target.id},
    )
    assert db_session.get(ConceptReviewItem, review.id) is not None


def test_concept_management_api_deletes_only_empty_history_free_concepts(
    client: TestClient,
    db_session: Session,
) -> None:
    concept = _add_concept(db_session, "temporary concept")
    db_session.add(
        ConceptDecisionEvent(
            subject_id=concept.subject_id,
            action="concept_creation",
            affected_concept_ids_json=json.dumps([concept.id * 10]),
            affected_question_ids_json="[]",
            rationale="History for another concept with a prefix-overlapping id.",
            actor="app",
            source="web",
            metadata_json="{}",
        )
    )
    db_session.commit()

    response = client.delete(f"/api/concepts/{concept.id}")

    assert response.status_code == 204
    assert db_session.get(Concept, concept.id) is None


def test_concept_management_api_returns_422_for_cross_subject_parent(
    client: TestClient,
    db_session: Session,
) -> None:
    source = _concept(db_session, "pytest basics")
    other_subject = Subject(name="other", slug="other")
    parent = Concept(
        subject=other_subject,
        name="foreign parent",
        slug="foreign-parent",
        normalized_name=normalize_concept_name("foreign parent", subject_name=other_subject.name),
        status=ConceptStatus.ACTIVE,
    )
    db_session.add(parent)
    db_session.commit()

    response = client.post(
        f"/api/subjects/{source.subject_id}/concepts",
        json={
            "name": "invalid child",
            "parent_concept_id": parent.id,
            "rationale": "Should be rejected as a client error.",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Parent concept must belong to the same subject."


def _concept(db_session: Session, name: str) -> Concept:
    concept = db_session.scalar(select(Concept).where(Concept.name == name))
    assert concept is not None
    return concept


def _add_concept(db_session: Session, name: str) -> Concept:
    source = _concept(db_session, "pytest basics")
    concept = Concept(
        subject=source.subject,
        name=name,
        slug=name.replace(" ", "-"),
        normalized_name=normalize_concept_name(name, subject_name=source.subject.name),
        status=ConceptStatus.ACTIVE,
    )
    db_session.add(concept)
    db_session.commit()
    db_session.refresh(concept)
    return concept
