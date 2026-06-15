import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Concept, ConceptStatus
from app.services.concept_service import normalize_concept_name
from app.services.grading_service import get_mastery_rows
from app.services.subject_service import build_subject_mastery_groups

pytestmark = pytest.mark.api


def test_mastery_page_marks_parent_and_child_concepts(
    client: TestClient,
    db_session: Session,
) -> None:
    parent = _concept(db_session, "pytest basics")
    _add_child_concept(db_session, parent=parent, name="fixture workflows")

    response = client.get("/mastery")

    assert response.status_code == 200
    assert "Parent concept" not in response.text
    assert "Sub-concept" not in response.text
    assert 'data-depth="2"' in response.text
    assert 'aria-hidden="true"' in response.text


def test_mastery_keeps_active_concepts_with_missing_parents(
    db_session: Session,
) -> None:
    parent = _concept(db_session, "pytest basics")
    archived_parent = _add_concept(
        db_session,
        parent=parent,
        name="archived parent",
        status=ConceptStatus.ARCHIVED,
    )
    _add_concept(
        db_session,
        parent=archived_parent,
        name="orphaned active child",
    )

    mastery_group = build_subject_mastery_groups(
        get_mastery_rows(db_session, subject_id=parent.subject_id)
    )[0]

    assert "orphaned active child" in [row.concept.name for row in mastery_group.mastery_rows]
    assert "orphaned active child" in [row.concept.name for row in mastery_group.root_rows]


def _concept(db_session: Session, name: str) -> Concept:
    concept = db_session.scalar(select(Concept).where(Concept.name == name))
    assert concept is not None
    return concept


def _add_child_concept(db_session: Session, *, parent: Concept, name: str) -> Concept:
    return _add_concept(db_session, parent=parent, name=name)


def _add_concept(
    db_session: Session,
    *,
    parent: Concept,
    name: str,
    status: ConceptStatus = ConceptStatus.ACTIVE,
) -> Concept:
    concept = Concept(
        subject=parent.subject,
        parent=parent,
        name=name,
        slug=name.replace(" ", "-"),
        normalized_name=normalize_concept_name(name, subject_name=parent.subject.name),
        status=status,
    )
    db_session.add(concept)
    db_session.commit()
    db_session.refresh(concept)
    return concept
