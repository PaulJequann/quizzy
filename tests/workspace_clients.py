from fastapi.testclient import TestClient

from app.auth.models import User
from app.database import get_session_factory
from app.main import create_app
from app.study_access import StudyAccess
from app.workspace_database import workspace_session

PARENT_ID = 1


def create_learner(email: str, name: str) -> int:
    with get_session_factory()() as accounts:
        learner = User(
            email=email,
            hashed_password="!",
            display_name=name,
            is_active=True,
            is_superuser=False,
        )
        accounts.add(learner)
        accounts.commit()
        return learner.id


def reviewer_session(learner_id: int):
    return workspace_session(StudyAccess(PARENT_ID, learner_id, True, "Child", True))


def learner_session(learner_id: int):
    return workspace_session(StudyAccess(learner_id, learner_id, True, "Child", False))


def reviewer_browser(learner_id: int | None = None) -> TestClient:
    browser = TestClient(
        create_app(), headers={"origin": "http://testserver"}, follow_redirects=False
    )
    login = browser.post(
        "/auth/login", data={"username": "parent@example.com", "password": "test-password"}
    )
    assert login.status_code == 204
    if learner_id is not None:
        browser.cookies.set("quizzy_workspace", str(learner_id))
    return browser


def learner_browser(learner_id: int) -> TestClient:
    from app.auth.users import current_user

    app = create_app()
    app.dependency_overrides[current_user] = lambda: User(
        id=learner_id, is_active=True, is_superuser=False
    )
    return TestClient(app, headers={"origin": "http://testserver"}, follow_redirects=False)
