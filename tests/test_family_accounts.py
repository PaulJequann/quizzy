import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import Subject

pytestmark = pytest.mark.integration


def test_private_routes_require_login(db_session):
    with TestClient(create_app(), follow_redirects=False) as browser:
        assert browser.get("/quizzes").status_code in {303, 401}
        assert browser.get("/api/quizzes").status_code == 401


def test_subject_identity_is_private_to_workspace(db_session):
    from app.auth.models import User
    from app.study_access import resolve_study_access
    from app.workspace_database import workspace_session

    parent = User(
        email="parent@example.com",
        hashed_password="disabled",
        is_active=True,
        is_superuser=True,
        display_name="Parent",
    )
    child = User(
        email="child@example.com",
        hashed_password="disabled",
        is_active=True,
        is_superuser=False,
        display_name="Child",
    )
    from app.database import get_session_factory

    parent.email = "otherparent@example.com"
    with get_session_factory()() as accounts:
        accounts.add_all([parent, child])
        accounts.commit()
        own_access = resolve_study_access(accounts, parent)
        other_access = resolve_study_access(accounts, child)
    with workspace_session(own_access) as own:
        own.add(Subject(name="Private", slug="same"))
        own.commit()
        private_id = own.scalar(select(Subject.id).where(Subject.slug == "same"))
    with workspace_session(other_access) as other:
        assert other.get(Subject, private_id) is None
        assert other.scalar(select(func.count(Subject.id))) == 0
        other.add(Subject(name="Child", slug="same"))
        other.commit()


def test_cookie_mutations_require_same_origin(db_session):
    with TestClient(create_app()) as browser:
        credentials = {"username": "parent@example.com", "password": "test-password"}
        assert browser.post("/auth/login", data=credentials).status_code == 403
        assert (
            browser.post(
                "/auth/login", data=credentials, headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            browser.post(
                "/auth/login", data=credentials, headers={"origin": "http://testserver"}
            ).status_code
            == 204
        )
        assert browser.post("/auth/logout").status_code == 403
        assert browser.get("/quizzes").status_code == 200
        assert (
            browser.post("/auth/logout", headers={"origin": "http://testserver"}).status_code == 204
        )
        assert browser.get("/api/quizzes").status_code == 401


def test_learner_can_manage_private_study_content(db_session):
    from fastapi_users.password import PasswordHelper

    from app.auth.models import User
    from app.database import get_session_factory
    from app.models import Quiz, QuizStatus
    from app.study_access import resolve_study_access
    from app.workspace_database import workspace_session

    with get_session_factory()() as accounts:
        learner = User(
            email="learner@example.com",
            hashed_password=PasswordHelper().hash("learner-secret"),
            display_name="Learner",
            is_active=True,
            is_superuser=False,
        )
        accounts.add(learner)
        accounts.commit()
        access = resolve_study_access(accounts, learner)
    with workspace_session(access) as private:
        draft = Quiz(title="Secret draft", status=QuizStatus.DRAFT)
        private.add(draft)
        private.commit()
        draft_id = draft.id
    with TestClient(create_app(), headers={"origin": "http://testserver"}) as browser:
        assert (
            browser.post(
                "/auth/login", data={"username": learner.email, "password": "learner-secret"}
            ).status_code
            == 204
        )
        assert "Secret draft" in browser.get("/quizzes").text
        assert browser.get(f"/quizzes/{draft_id}/take").status_code == 400
        assert browser.get("/subjects").status_code == 200
        assert browser.get("/quizzes/new").status_code == 200
        assert browser.get("/api/quizzes").status_code == 200
        created = browser.post(
            "/api/quizzes",
            json={"title": "Learner-made practice", "status": "published"},
        )
        assert created.status_code == 201
        assert browser.get(f"/quizzes/{created.json()['id']}/questions/new").status_code == 200
        assert browser.get("/accounts").status_code == 403
        browser.cookies.set("quizzy_workspace", "1")
        assert browser.get("/quizzes").status_code == 403


def test_scope_rejects_sql_and_ownership_changes(db_session):
    from sqlalchemy import text

    with pytest.raises(ValueError):
        db_session.execute(text("SELECT * FROM subjects"))
    with pytest.raises(ValueError):
        db_session.scalars(select(Subject).from_statement(text("SELECT * FROM subjects")))
    subject = db_session.scalar(select(Subject))
    subject.workspace_id = 222
    with pytest.raises(ValueError):
        db_session.commit()
    db_session.rollback()


def test_grading_rejects_choice_from_another_question(db_session):
    from app.models import Choice, Question, Quiz
    from app.services.grading_service import submit_attempt

    question = Question(stem="Other", subject_id=1)
    question.choices.append(Choice(text="Foreign choice", is_correct=True))
    db_session.add(question)
    db_session.commit()
    quiz = db_session.scalar(select(Quiz))
    with pytest.raises(ValueError, match="choice"):
        submit_attempt(db_session, quiz, {quiz.questions[0].id: question.choices[0].id})


def test_bootstrap_claims_inactive_parent_once(db_session):
    from app.auth.models import User
    from app.auth.provision import FamilySetup, provision_family
    from app.database import get_session_factory

    with get_session_factory()() as accounts:
        accounts.add(
            User(
                email="legacy-parent@invalid.local",
                hashed_password="!",
                is_active=False,
                is_superuser=True,
                display_name="Parent",
            )
        )
        accounts.commit()
    parent_id, learner_id = provision_family(
        FamilySetup(
            "family@example.com",
            "parent12",
            "kid@example.com",
            "learner1",
            "Kid",
        )
    )
    assert parent_id != learner_id
    with pytest.raises(ValueError):
        provision_family(
            FamilySetup(
                "family@example.com",
                "parent12",
                "kid@example.com",
                "learner1",
                "Kid",
            )
        )
    with TestClient(create_app(), headers={"origin": "http://testserver"}) as browser:
        assert (
            browser.post(
                "/auth/login",
                data={"username": "family@example.com", "password": "parent12"},
            ).status_code
            == 204
        )
        browser.cookies.set("quizzy_workspace", str(learner_id))
        assert browser.get("/quizzes").status_code == 200
        assert browser.post("/quizzes/1/submit").status_code == 403


@pytest.mark.usefixtures("learner_client")
def test_progress_links_to_real_results_and_teaching_contains_misses(client, db_session):
    from app.models import Quiz
    from app.services.grading_service import submit_attempt
    from app.services.teaching_brief_service import prepare_next_practice

    quiz = db_session.scalar(select(Quiz))
    wrong = next(choice for choice in quiz.questions[0].choices if not choice.is_correct)
    attempt = submit_attempt(db_session, quiz, {quiz.questions[0].id: wrong.id})
    progress = client.get("/progress")
    assert progress.status_code == 200
    assert f"/attempts/{attempt.id}/results" in progress.text
    brief = prepare_next_practice(db_session, 1)
    assert brief["recent_misses"][0]["question"] == "What does pytest run?"
    assert brief["recent_misses"][0]["selected_answer"] == "Backups"
    assert brief["recent_misses"][0]["correct_answer"] == "Tests"


def test_historical_results_survive_archiving(client, db_session):
    from app.auth.models import User
    from app.database import get_session_factory
    from app.models import Quiz, QuizStatus
    from app.services.grading_service import submit_attempt

    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    quiz.status = QuizStatus.ARCHIVED
    db_session.commit()
    with get_session_factory()() as accounts:
        parent = accounts.get(User, 1)
        assert parent is not None
        parent.is_superuser = False
        accounts.commit()
    assert client.get(f"/quizzes/{quiz.id}/take").status_code == 200
    assert client.get(f"/attempts/{attempt.id}/results").status_code == 200
    assert client.get("/progress").status_code == 200


def test_seed_command_requires_and_uses_explicit_workspace(db_session, monkeypatch, capsys):
    import sys

    from scripts.seed import main

    monkeypatch.setattr(sys, "argv", ["seed", "--workspace-id", "1"])
    main()
    from app.models import Quiz

    assert db_session.scalar(select(func.count(Quiz.id))) > 1
    assert "Created" in capsys.readouterr().out
