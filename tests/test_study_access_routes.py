import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.models import User
from app.database import get_session_factory
from app.main import create_app
from app.mcp.access import authorize_mcp_workspace
from app.models import Quiz
from app.services.grading_service import submit_attempt

pytestmark = pytest.mark.integration


@pytest.fixture
def study_browser(db_session):
    browser = TestClient(create_app(), headers={"origin": "http://testserver"})
    assert (
        browser.post(
            "/auth/login", data={"username": "parent@example.com", "password": "test-password"}
        ).status_code
        == 204
    )
    with browser:
        yield browser


def make_preview(browser):
    with get_session_factory()() as accounts:
        reviewer = User(
            email="reviewer@example.com",
            hashed_password="!",
            display_name="Reviewer",
            is_active=True,
            is_superuser=True,
        )
        accounts.add(reviewer)
        accounts.commit()
        reviewer_id = reviewer.id
    from app.auth.users import current_user

    browser.app.dependency_overrides[current_user] = lambda: User(
        id=reviewer_id, is_active=True, is_superuser=True
    )
    browser.cookies.set("quizzy_workspace", "1")


def make_learner():
    with get_session_factory()() as accounts:
        user = accounts.get(User, 1)
        assert user is not None
        user.is_superuser = False
        accounts.commit()


@pytest.mark.parametrize(
    "path",
    [
        "/quizzes/1/submit",
        "/api/quizzes/1/attempts",
        "/questions/1/report-issue",
        "/api/questions/1/report-issue",
        "/attempt-answers/1/retrieval-feedback",
        "/api/attempt-answers/1/retrieval-feedback",
    ],
)
def test_preview_writes_leave_all_workspace_rows_unchanged(study_browser, db_session, path):
    from sqlalchemy import text

    from app.database import get_engine
    from tests.db_utils import _app_table_names

    quiz = db_session.scalar(select(Quiz))
    submit_attempt(db_session, quiz, {})
    make_preview(study_browser)

    def snapshot():
        with get_engine().connect() as connection:
            return {
                table: connection.execute(text(f"SELECT * FROM {table}")).fetchall()
                for table in _app_table_names(get_engine())
                if table not in {"users", "access_tokens"}
            }

    before = snapshot()
    response = study_browser.post(path, json={"category": "unclear_wording"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Preview cannot change learner progress"
    assert snapshot() == before


def test_feedback_route_matrix_and_controls(study_browser, db_session):
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    answer = attempt.answers[0]
    take_url = f"/quizzes/{quiz.id}/take"
    assert "data-quick-flag-button\n" in study_browser.get(take_url).text
    make_learner()
    assert (
        study_browser.post(
            f"/api/quizzes/{quiz.id}/attempts",
            json={"answers": [{"question_id": 1, "selected_choice_id": None}]},
        ).status_code
        == 201
    )
    assert study_browser.post("/api/questions/1/report-issue", json={}).status_code == 422
    assert study_browser.post("/questions/1/report-issue", data={}).status_code == 422
    report = study_browser.post(
        "/questions/1/report-issue",
        data={
            "category": "unclear_wording",
            "attempt_answer_id": str(answer.id),
            "note": "Ambiguous",
        },
    )
    assert report.status_code == 200
    assert (
        study_browser.post(
            f"/api/attempt-answers/{answer.id}/retrieval-feedback",
            json={
                "retrieval_friction_rating": 1,
            },
        ).status_code
        == 200
    )
    assert "data-quick-flag-button\n" in study_browser.get(take_url).text
    assert "Reviewer preview" not in study_browser.get(take_url).text


def test_preview_ui_keeps_management_and_hides_feedback(study_browser, db_session):
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    make_preview(study_browser)
    page = study_browser.get(f"/quizzes/{quiz.id}/take").text
    assert "Reviewer preview" in page
    assert "data-preview-only disabled" in page
    assert "data-quick-flag-button\n" not in page
    results = study_browser.get(f"/attempts/{attempt.id}/results").text
    assert 'name="retrieval_friction_rating"' not in results
    assert "/questions/1/report-issue" not in results
    assert study_browser.get("/teaching").status_code == 200
    assert study_browser.get("/subjects").status_code == 200


@pytest.mark.parametrize("target_id", [999, 2])
def test_target_error_mapping(study_browser, db_session, target_id):
    with get_session_factory()() as accounts:
        accounts.add(
            User(
                id=2,
                email="inactive@example.com",
                hashed_password="!",
                is_active=False,
                display_name="Inactive",
            )
        )
        accounts.commit()
    study_browser.cookies.set("quizzy_workspace", str(target_id))
    response = study_browser.get("/quizzes")
    assert response.status_code == 403
    assert response.json()["detail"] == "Inactive workspace"
    assert (
        study_browser.post("/accounts/select", data={"workspace_id": target_id}).status_code == 404
    )
    with pytest.raises(ValueError, match="Unknown active workspace"):
        authorize_mcp_workspace(target_id)


def test_invalid_cookie_and_learner_cannot_probe_target(study_browser, db_session):
    study_browser.cookies.set("quizzy_workspace", "not-an-id")
    assert study_browser.get("/quizzes").status_code == 400
    make_learner()
    study_browser.cookies.set("quizzy_workspace", "999")
    response = study_browser.get("/quizzes")
    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace access denied"
