import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Choice, Question, Quiz, QuizQuestion
from app.services.grading_service import submit_attempt
from app.workspace_database import workspace_session

pytestmark = pytest.mark.integration


def test_results_show_only_submitted_question_snapshots(client, db_session):
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    original = quiz.questions[0]
    original.stem = "Changed live question"
    original.choices[0].text = "Changed live answer"
    quiz.question_links.clear()
    replacement = Question(stem="Unanswered new question", subject_id=1)
    replacement.choices.append(Choice(text="Unanswered secret", is_correct=True))
    quiz.question_links.append(QuizQuestion(question=replacement, order_index=1))
    db_session.commit()
    page = client.get(f"/attempts/{attempt.id}/results")
    assert page.status_code == 200
    assert "What does pytest run?" in page.text
    assert "Unanswered new question" not in page.text
    assert "Unanswered secret" not in page.text
    assert "Changed live question" not in page.text
    assert "Changed live answer" not in page.text


@pytest.mark.parametrize("mutation", ["question", "choice", "new_choice", "membership"])
def test_mcp_session_cannot_mutate_published_content(db_session, mutation):
    with workspace_session(db_session.access, drafts_only=True) as agent:
        quiz = agent.scalar(select(Quiz))
        if mutation == "question":
            quiz.questions[0].stem = "Changed by agent"
        elif mutation == "choice":
            quiz.questions[0].choices[0].text = "Changed by agent"
        elif mutation == "new_choice":
            quiz.questions[0].choices.append(Choice(text="New live answer"))
        else:
            quiz.question_links.clear()
        with pytest.raises(ValueError, match="published"):
            agent.commit()


def test_login_accepts_configured_https_origin_behind_proxy(db_session, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("APP_BASE_URL", "https://quizzy.example.com")
    get_settings.cache_clear()
    with TestClient(create_app(), base_url="http://internal-proxy") as browser:
        credentials = {"username": "parent@example.com", "password": "test-password"}
        assert (
            browser.post(
                "/auth/login", data=credentials, headers={"origin": "https://quizzy.example.com"}
            ).status_code
            == 204
        )
        assert (
            browser.post("/auth/logout", headers={"origin": "http://internal-proxy"}).status_code
            == 403
        )


@pytest.mark.usefixtures("learner_client")
def test_learner_result_feedback_uses_actual_json_endpoint(client, db_session):
    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    answer = attempt.answers[0]
    response = client.post(
        f"/api/attempt-answers/{answer.id}/retrieval-feedback",
        json={"retrieval_friction_rating": 1},
    )
    assert response.status_code == 200
    assert (
        client.post(
            "/api/questions/1/report-issue", json={"category": "unclear_wording"}
        ).status_code
        == 200
    )


@pytest.mark.usefixtures("learner_client")
def test_teaching_labels_missing_historical_snapshot(client, db_session):
    from app.services.teaching_brief_service import prepare_next_practice

    quiz = db_session.scalar(select(Quiz))
    attempt = submit_attempt(db_session, quiz, {})
    attempt.answers[0].question_stem_snapshot = ""
    db_session.commit()
    brief = prepare_next_practice(db_session, 1)
    assert brief["recent_misses"][0]["question"] == "What does pytest run?"
    assert brief["recent_misses"][0]["question_text_source"] == "current_question"


def test_mcp_cannot_move_choice_out_of_published_question(db_session):
    from app.models import Question

    destination = Question(stem="Draft bank question", subject_id=1)
    db_session.add(destination)
    db_session.commit()
    with workspace_session(db_session.access, drafts_only=True) as agent:
        quiz = agent.scalar(select(Quiz))
        choice = quiz.questions[0].choices[0]
        choice.question_id = destination.id
        with pytest.raises(ValueError, match="published"):
            agent.commit()
