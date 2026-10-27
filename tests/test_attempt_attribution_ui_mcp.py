import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.database import get_session_factory
from app.mcp.server import build_server
from app.models import Attempt, AttemptRole, ExamAssignment, ExamSubmission, Quiz, utc_now
from app.services.exam_paper import paper_from_quiz, paper_json
from tests.conftest import seed_test_quiz
from tests.test_open_ended_exam_grading import RELEASE_FORM, seed_mixed_exam, submitted_exam
from tests.workspace_clients import (
    create_learner,
    learner_browser,
    learner_session,
    reviewer_browser,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def learner_study(db_session):
    learner_id = create_learner("attribution@example.com", "Attribution learner")
    with learner_session(learner_id) as db:
        seed_test_quiz(db)
        quiz = db.scalar(select(Quiz))
        assert quiz is not None
        quiz_id = quiz.id
        subject_id = quiz.primary_subject_id
    return learner_id, quiz_id, subject_id


def seed_history(workspace_id: int, quiz_id: int, administrator_id: int) -> dict[str, int]:
    with get_session_factory()() as db:
        attempts = [
            Attempt(
                workspace_id=workspace_id,
                quiz_id=quiz_id,
                actor_id=actor_id,
                actor_role=role,
                score=100,
                correct_count=1,
                total_questions=1,
                submitted_at=utc_now(),
            )
            for role, actor_id in (
                (AttemptRole.LEARNER, workspace_id),
                (AttemptRole.TEST, administrator_id),
                (AttemptRole.UNKNOWN, None),
            )
        ]
        db.add_all(attempts)
        db.commit()
        return {attempt.actor_role: attempt.id for attempt in attempts}


def test_api_and_quiz_results_identify_administrator_test_attempt(client, db_session):
    quiz = db_session.scalar(select(Quiz))
    question = quiz.questions[0]
    actor_id = db_session.access.actor_id
    take = client.get(f"/quizzes/{quiz.id}/take")
    assert "Test attempt" in take.text
    assert "excluded from learner mastery" in take.text

    created = client.post(
        f"/api/quizzes/{quiz.id}/attempts",
        json={
            "answers": [{"question_id": question.id, "selected_choice_id": question.choices[0].id}]
        },
    )
    assert created.status_code == 201
    attempt = created.json()
    assert (attempt["actor_id"], attempt["actor_role"]) == (actor_id, "test")
    assert attempt["actor_name"] == "Parent"
    result = client.get(f"/attempts/{attempt['id']}/results")
    assert result.status_code == 200
    assert "Test attempt" in result.text
    assert f"User ID {actor_id}" in result.text
    assert "This attempt is excluded from learner mastery." in result.text
    assert "Retrieval feel" not in result.text


def test_learner_api_and_web_submission_keep_learner_attribution(learner_study):
    learner_id, quiz_id, _ = learner_study
    browser = learner_browser(learner_id)
    assert "Test attempt" not in browser.get(f"/quizzes/{quiz_id}/take").text
    with learner_session(learner_id) as db:
        quiz = db.get(Quiz, quiz_id)
        question = quiz.questions[0]
        question_id, choice_id = question.id, question.choices[0].id
    created = browser.post(
        f"/api/quizzes/{quiz_id}/attempts",
        json={"answers": [{"question_id": question_id, "selected_choice_id": choice_id}]},
    )
    assert created.status_code == 201
    assert (created.json()["actor_id"], created.json()["actor_role"]) == (
        learner_id,
        "learner",
    )
    assert created.json()["actor_name"] == "Attribution learner"
    submitted = browser.post(
        f"/quizzes/{quiz_id}/submit", data={f"question_{question_id}": str(choice_id)}
    )
    assert submitted.status_code == 303
    result = browser.get(submitted.headers["location"])
    assert result.status_code == 200
    assert "Learner attempt" in result.text
    assert f"User ID {learner_id}" in result.text
    assert "This attempt is excluded" not in result.text
    assert "Retrieval feel" in result.text


def test_history_keeps_all_roles_and_names_unknown_actor(learner_study, db_session):
    learner_id, quiz_id, _ = learner_study
    attempts = seed_history(learner_id, quiz_id, db_session.access.actor_id)
    browser = learner_browser(learner_id)
    history = browser.get("/progress")
    assert history.status_code == 200
    for role, attempt_id in attempts.items():
        assert f"{role.title()} attempt" in history.text
        assert f"/attempts/{attempt_id}/results" in history.text
    assert "Only learner attempts count toward mastery." in history.text
    assert "Unknown user ID" in history.text
    result = browser.get(f"/attempts/{attempts['unknown']}/results")
    assert result.status_code == 200
    assert "Unknown attempt" in result.text
    assert "Unknown user ID" in result.text
    assert "This attempt is excluded from learner mastery." in result.text
    assert "Retrieval feel" not in result.text


def test_mcp_history_is_scoped_filterable_and_bounded(learner_study, db_session):
    learner_id, quiz_id, _ = learner_study
    administrator_id = db_session.access.actor_id
    attempts = seed_history(learner_id, quiz_id, administrator_id)
    private_quiz = db_session.scalar(select(Quiz))
    seed_history(administrator_id, private_quiz.id, administrator_id)

    async def exercise():
        async with Client(build_server()) as client:
            result = await client.call_tool("list_attempts", {"workspace_id": learner_id})
            rows = result.data["attempts"]
            assert [row["id"] for row in rows] == list(reversed(attempts.values()))
            assert {row["actor_role"] for row in rows} == {"learner", "test", "unknown"}
            assert all(row["quiz_id"] == quiz_id for row in rows)
            assert rows[0]["actor_id"] is None
            assert rows[0]["actor_name"] is None
            assert rows[1]["actor_id"] == administrator_id
            assert rows[1]["actor_name"] == "Parent"
            assert rows[2]["actor_id"] == learner_id
            assert rows[2]["actor_name"] == "Attribution learner"
            assert rows[0]["submitted_at"] is not None
            assert rows[0]["total_questions"] == 1
            assert rows[0]["score"] == 100
            for role in attempts:
                result = await client.call_tool(
                    "list_attempts", {"workspace_id": learner_id, "actor_role": role}
                )
                assert [row["id"] for row in result.data["attempts"]] == [attempts[role]]
            page = await client.call_tool(
                "list_attempts", {"workspace_id": learner_id, "limit": 1, "offset": 1}
            )
            assert [row["id"] for row in page.data["attempts"]] == [attempts["test"]]
            for invalid in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"actor_role": "admin"}):
                with pytest.raises(ToolError):
                    await client.call_tool("list_attempts", {"workspace_id": learner_id, **invalid})
            with pytest.raises(ToolError):
                await client.call_tool("list_attempts", {})
            with pytest.raises(ToolError):
                await client.call_tool("list_attempts", {"workspace_id": -1})

    asyncio.run(exercise())


def test_mcp_mastery_declares_learner_only_evidence(learner_study):
    learner_id, _, subject_id = learner_study

    async def exercise():
        async with Client(build_server()) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            for name in ("get_mastery_focus", "get_subject_mastery_tree"):
                assert "learner-only" in tools[name].description
                result = await client.call_tool(
                    name, {"workspace_id": learner_id, "subject_id": subject_id}
                )
                assert result.data["evidence_policy"] == "learner_only"
            brief = await client.call_tool("prepare_next_practice", {"workspace_id": learner_id})
            assert brief.data["evidence_policy"] == "learner_only"
            assert tools["list_attempts"].annotations.readOnlyHint is True

    asyncio.run(exercise())


@pytest.mark.parametrize("actor_role", list(AttemptRole))
def test_exam_review_result_and_mcp_packet_show_attribution(db_session, actor_role):
    exam = submitted_exam()
    actor_id = {
        AttemptRole.LEARNER: exam.learner_id,
        AttemptRole.TEST: db_session.access.actor_id,
        AttemptRole.UNKNOWN: None,
    }[actor_role]
    with get_session_factory()() as fixture_db:
        submission = fixture_db.scalar(
            select(ExamSubmission).where(ExamSubmission.assignment_id == exam.exam_id)
        )
        assert submission is not None
        assert (submission.actor_id, submission.actor_role) == (
            exam.learner_id,
            AttemptRole.LEARNER,
        )
        submission.actor_id, submission.actor_role = actor_id, actor_role
        fixture_db.commit()
    review = exam.reviewer.get(f"/exams/{exam.exam_id}")
    assert review.status_code == 200
    actor_label = f"User ID {actor_id}" if actor_id is not None else "Unknown user ID"
    actor_name = {
        AttemptRole.LEARNER: "Child",
        AttemptRole.TEST: "Parent",
        AttemptRole.UNKNOWN: None,
    }[actor_role]
    assert f"{actor_role.title()} submission" in review.text
    assert actor_label in review.text
    if actor_name is not None:
        assert actor_name in review.text
    if actor_role != AttemptRole.LEARNER:
        assert "This submission is excluded from learner mastery." in review.text
    pending = exam.learner.get(f"/exams/{exam.exam_id}")
    assert pending.status_code == 200
    assert f"{actor_role.title()} submission" in pending.text
    assert actor_label in pending.text

    async def exercise():
        async with Client(build_server()) as client:
            pending = await client.call_tool(
                "list_pending_exam_submissions", {"workspace_id": exam.learner_id}
            )
            row = pending.data["submissions"][0]
            assert (row["actor_id"], row["actor_role"]) == (actor_id, actor_role)
            assert row["actor_name"] == actor_name
            packet = await client.call_tool(
                "get_exam_submission_for_grading",
                {"workspace_id": exam.learner_id, "exam_id": exam.exam_id},
            )
            assert (packet.data["actor_id"], packet.data["actor_role"]) == (
                actor_id,
                actor_role,
            )
            assert packet.data["actor_name"] == actor_name
            assert packet.data["evidence_policy"] == "learner_only"

    asyncio.run(exercise())
    released = exam.reviewer.post(f"/exams/{exam.exam_id}/release", data=RELEASE_FORM)
    assert released.status_code == 303
    with get_session_factory()() as fixture_db:
        attempt = fixture_db.scalar(
            select(Attempt)
            .join(ExamSubmission, ExamSubmission.id == Attempt.exam_submission_id)
            .where(ExamSubmission.assignment_id == exam.exam_id)
        )
        assert attempt is not None
        assert (attempt.actor_id, attempt.actor_role) == (actor_id, actor_role)
    result = exam.learner.get(f"/exams/{exam.exam_id}")
    assert result.status_code == 200
    assert f"{actor_role.title()} submission" in result.text
    assert actor_label in result.text
    if actor_role != AttemptRole.LEARNER:
        assert "This submission is excluded from learner mastery." in result.text


def test_administrator_exam_submission_is_attributed_as_test(db_session):
    administrator_id = db_session.access.actor_id
    quiz_id = seed_mixed_exam(administrator_id)
    quiz = db_session.get(Quiz, quiz_id)
    assert quiz is not None
    paper = paper_from_quiz(quiz)
    assignment = ExamAssignment(
        assigned_by_id=administrator_id,
        source_quiz_id=quiz_id,
        title_snapshot=quiz.title,
        paper_version=paper.version,
        paper_json=paper_json(paper),
        request_key=uuid4(),
    )
    db_session.add(assignment)
    db_session.commit()
    browser = reviewer_browser()

    response = browser.post(
        f"/exams/{assignment.id}/submit", data={"answer_1": "1", "answer_2": "Testing"}
    )

    assert response.status_code == 303
    with get_session_factory()() as fixture_db:
        submission = fixture_db.scalar(
            select(ExamSubmission).where(ExamSubmission.assignment_id == assignment.id)
        )
        assert submission is not None
        assert (submission.actor_id, submission.actor_role) == (
            administrator_id,
            AttemptRole.TEST,
        )


def test_fresh_process_mcp_exposes_attributed_history(learner_study, db_session):
    learner_id, quiz_id, _ = learner_study
    attempts = seed_history(learner_id, quiz_id, db_session.access.actor_id)

    async def exercise():
        transport = StdioTransport(
            command="uv",
            args=["run", "python", "mcp_server.py"],
            env=os.environ.copy(),
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        async with Client(transport) as client:
            result = await client.call_tool(
                "list_attempts", {"workspace_id": learner_id, "actor_role": "unknown"}
            )
            assert [row["id"] for row in result.data["attempts"]] == [attempts["unknown"]]
            assert result.data["attempts"][0]["actor_id"] is None

    asyncio.run(exercise())
