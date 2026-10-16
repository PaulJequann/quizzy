from dataclasses import FrozenInstanceError
from typing import Annotated
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth.boundary import request_access, study_operation
from app.auth.models import User
from app.auth.users import current_user
from app.main import create_app
from app.models import QuizStatus
from app.study_access import (
    InactiveWorkspace,
    StudyAccess,
    StudyAccessDenied,
    StudyOperation,
    resolve_study_access,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("access", "allowed"),
    [
        (StudyAccess(1, 1, True, "Teacher", True), [True, True, True, True, True, False]),
        (StudyAccess(1, 2, True, "Student", True), [True, True, False, False, False, True]),
        (StudyAccess(2, 2, True, "Student"), [True, True, True, True, False, False]),
    ],
)
def test_operation_matrix(access, allowed):
    assert [access.allows(operation) for operation in StudyOperation] == allowed
    assert access.is_preview == (access.workspace_id != access.actor_id)
    with pytest.raises(FrozenInstanceError):
        access.workspace_name = "Changed"


@pytest.mark.parametrize("status", list(QuizStatus))
def test_publication_does_not_gate_history(status):
    learner = StudyAccess(2, 2, True, "Student")
    reviewer = StudyAccess(1, 2, True, "Student", True)
    assert learner.can_view_quiz(status)
    assert reviewer.can_view_quiz(status)
    assert learner.allows(StudyOperation.VIEW)


@pytest.mark.parametrize(
    ("access", "without_answer", "with_answer"),
    [
        (StudyAccess(1, 1, True, "Teacher", True), True, True),
        (StudyAccess(1, 2, True, "Student", True), False, False),
        (StudyAccess(2, 2, True, "Student"), True, True),
    ],
)
def test_question_reporting_needs_own_progress_and_learner_context(
    access, without_answer, with_answer
):
    assert access.can_report_question(None) is without_answer
    assert access.can_report_question(42) is with_answer


def test_all_private_routes_declare_an_operation():
    private = [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute)
        and any(dependency.call is request_access for dependency in route.dependant.dependencies)
    ]
    assert len(private) >= 40
    for route in private:
        assert isinstance(getattr(route.endpoint, "study_operation", None), StudyOperation), (
            route.path
        )


def test_marker_preserves_handler_and_rejects_duplicate_intent():
    def endpoint():
        return "handler"

    assert study_operation(StudyOperation.VIEW)(endpoint) is endpoint
    with pytest.raises(ValueError, match="already declared"):
        study_operation(StudyOperation.MANAGE)(endpoint)


@pytest.mark.parametrize("preview", [False, True])
def test_unmarked_route_fails_closed_before_endpoint(preview, monkeypatch):
    access = StudyAccess(1, 2 if preview else 1, True, "Teacher", True)
    monkeypatch.setattr("app.auth.boundary.get_session_factory", MagicMock())
    monkeypatch.setattr("app.auth.boundary.resolve_study_access", lambda *args: access)
    app = FastAPI()
    app.dependency_overrides[current_user] = lambda: User(id=1, is_active=True, is_superuser=True)
    called = Mock()
    database_opened = Mock()

    def database():
        database_opened()

    @app.post("/new-progress-writer", dependencies=[Depends(request_access)])
    def unmarked(db: Annotated[None, Depends(database)]):
        called()

    response = TestClient(app).post("/new-progress-writer")
    assert response.status_code == 403
    assert response.json()["detail"] == "Study operation required"
    called.assert_not_called()
    database_opened.assert_not_called()


def test_route_aliases_share_declared_intent(monkeypatch):
    access = StudyAccess(2, 2, True, "Student")
    monkeypatch.setattr("app.auth.boundary.get_session_factory", MagicMock())
    monkeypatch.setattr("app.auth.boundary.resolve_study_access", lambda *args: access)
    app = FastAPI()
    app.dependency_overrides[current_user] = lambda: User(id=2, is_active=True)

    @app.get("/first", dependencies=[Depends(request_access)])
    @app.get("/alias", dependencies=[Depends(request_access)])
    @study_operation(StudyOperation.VIEW)
    def original():
        return "same view"

    browser = TestClient(app)
    assert browser.get("/first").json() == "same view"
    assert browser.get("/alias").json() == "same view"


@pytest.mark.parametrize(
    ("active", "admin", "requested", "message"),
    [(False, True, 2, "Inactive account"), (True, False, 2, "Workspace access denied")],
)
def test_authority_checked_before_target_lookup(active, admin, requested, message):
    accounts = Mock()
    actor = User(id=1, is_active=active, is_superuser=admin)
    with pytest.raises(StudyAccessDenied, match=message):
        resolve_study_access(accounts, actor, requested)
    accounts.scalar.assert_not_called()


def test_missing_active_target_has_domain_error():
    accounts = Mock()
    accounts.scalar.return_value = None
    with pytest.raises(InactiveWorkspace, match="Inactive workspace"):
        resolve_study_access(accounts, User(id=1, is_active=True, is_superuser=True), 2)
