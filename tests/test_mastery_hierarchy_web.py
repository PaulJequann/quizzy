import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Concept, ConceptStatus
from app.services.concept_service import normalize_concept_name

pytestmark = pytest.mark.api


def test_mastery_page_marks_parent_and_child_concepts(
    client: TestClient,
    db_session: Session,
) -> None:
    parent = _concept(db_session, "pytest basics")
    _add_child_concept(db_session, parent=parent, name="fixture workflows")

    response = client.get("/mastery")

    assert response.status_code == 200
    assert "Parent concept" in response.text
    assert "Sub-concept" in response.text
    assert 'data-depth="2"' in response.text


def _concept(db_session: Session, name: str) -> Concept:
    concept = db_session.scalar(select(Concept).where(Concept.name == name))
    assert concept is not None
    return concept


def _add_child_concept(db_session: Session, *, parent: Concept, name: str) -> Concept:
    concept = Concept(
        subject=parent.subject,
        parent=parent,
        name=name,
        slug=name.replace(" ", "-"),
        normalized_name=normalize_concept_name(name, subject_name=parent.subject.name),
        status=ConceptStatus.ACTIVE,
    )
    db_session.add(concept)
    db_session.commit()
    db_session.refresh(concept)
    return concept
