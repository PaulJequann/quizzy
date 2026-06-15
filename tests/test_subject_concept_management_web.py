import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Concept, ConceptStatus
from app.services.concept_service import normalize_concept_name

pytestmark = pytest.mark.api


def test_subject_concept_edit_form_renames_and_moves(
    client: TestClient,
    db_session: Session,
) -> None:
    concept = _concept(db_session, "pytest basics")
    parent = _add_concept(db_session, "pytest")

    response = client.post(
        f"/concepts/{concept.id}/update",
        data={
            "name": "pytest fundamentals",
            "parent_concept_id": str(parent.id),
            "rationale": "Clarify the taxonomy hierarchy.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/subjects/{concept.subject_id}"
    db_session.refresh(concept)
    assert concept.name == "pytest fundamentals"
    assert concept.parent_concept_id == parent.id


def test_subject_concept_edit_rejects_proposed_parent(
    client: TestClient,
    db_session: Session,
) -> None:
    concept = _concept(db_session, "pytest basics")
    proposed_parent = _add_concept(
        db_session, "proposed pytest parent", status=ConceptStatus.PROPOSED
    )

    detail_response = client.get(f"/subjects/{concept.subject_id}")
    response = client.post(
        f"/concepts/{concept.id}/update",
        data={
            "name": concept.name,
            "parent_concept_id": str(proposed_parent.id),
            "rationale": "Try unresolved parent.",
        },
        follow_redirects=False,
    )

    assert detail_response.status_code == 200
    assert f'value="{proposed_parent.id}"' not in detail_response.text
    assert response.status_code == 422
    db_session.refresh(concept)
    assert concept.parent_concept_id is None


def _concept(db_session: Session, name: str) -> Concept:
    concept = db_session.scalar(select(Concept).where(Concept.name == name))
    assert concept is not None
    return concept


def _add_concept(
    db_session: Session,
    name: str,
    *,
    status: ConceptStatus = ConceptStatus.ACTIVE,
) -> Concept:
    source = _concept(db_session, "pytest basics")
    concept = Concept(
        subject=source.subject,
        name=name,
        slug=name.replace(" ", "-"),
        normalized_name=normalize_concept_name(name, subject_name=source.subject.name),
        status=status,
    )
    db_session.add(concept)
    db_session.commit()
    db_session.refresh(concept)
    return concept
