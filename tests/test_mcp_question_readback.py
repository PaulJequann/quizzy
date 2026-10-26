import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from threading import Event

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.database import get_session_factory
from app.mcp.server import build_server
from app.models import (
    Attempt,
    AttemptAnswer,
    Choice,
    Difficulty,
    Question,
    QuestionKeyPoint,
    QuestionStatus,
    QuestionType,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.schemas import ChoiceCreate, DraftMultipleChoiceEdit
from app.services.quiz_service import update_draft_question
from app.workspace_database import workspace_session
from tests.workspace_clients import (
    create_learner,
    learner_browser,
    learner_session,
    reviewer_browser,
)

pytestmark = pytest.mark.mcp


def seed_draft_exam(db):
    subject = Subject(name="Readback", slug="readback")
    quiz = Quiz(
        title="Draft answer review",
        primary_subject=subject,
        quiz_type=QuizType.EXAM,
        difficulty=Difficulty.BEGINNER,
        status=QuizStatus.DRAFT,
    )
    choice = Question(
        subject=subject,
        stem="Which process makes sugar?",
        explanation="Photosynthesis makes sugar.",
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        exam_only=True,
        source_reference="science/chapter-1#plants",
        source_excerpt="Plants use light to make sugar.",
        choices=[
            Choice(text="Respiration", is_correct=False, order_index=1),
            Choice(text="Photosynthesis", is_correct=True, order_index=2),
            Choice(text="Digestion", is_correct=False, order_index=3),
            Choice(text="Evaporation", is_correct=False, order_index=4),
        ],
    )
    written = Question(
        subject=subject,
        stem="Explain photosynthesis.",
        explanation="Light energy supports sugar production.",
        question_type=QuestionType.OPEN_ENDED,
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        exam_only=True,
        model_answer="Plants use light to make sugar from water and carbon dioxide.",
        grading_notes="Accept glucose for sugar.",
        key_points=[
            QuestionKeyPoint(text="Uses light", order_index=1),
            QuestionKeyPoint(text="Makes sugar", order_index=2),
        ],
    )
    quiz.question_links = [
        QuizQuestion(question=written, order_index=1),
        QuizQuestion(question=choice, order_index=2),
    ]
    db.add(quiz)
    db.commit()
    return quiz.id, choice.id, written.id


def replacement_choices():
    return [
        {"text": "Sunlight", "is_correct": True},
        {"text": "Sand", "is_correct": False},
        {"text": "Salt", "is_correct": False},
        {"text": "Smoke", "is_correct": False},
    ]


def test_get_quiz_answer_readback_in_fresh_mcp_process(db_session):
    quiz_id, choice_id, written_id = seed_draft_exam(db_session)
    transport = StdioTransport(
        command="uv",
        args=["run", "python", "mcp_server.py"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    async def exercise():
        async with Client(transport) as client:
            ordinary = (
                await client.call_tool("get_quiz", {"workspace_id": 1, "quiz_id": quiz_id})
            ).data
            complete = (
                await client.call_tool(
                    "get_quiz",
                    {"workspace_id": 1, "quiz_id": quiz_id, "include_answers": True},
                )
            ).data
            assert [row["id"] for row in complete["questions"]] == [written_id, choice_id]
            assert all("choices" not in row for row in ordinary["questions"])
            assert all("explanation" not in row for row in ordinary["questions"])
            assert all(
                field not in row
                for row in ordinary["questions"]
                for field in ("model_answer", "key_points", "grading_notes")
            )
            written, multiple_choice = complete["questions"]
            assert written["model_answer"] == (
                "Plants use light to make sugar from water and carbon dioxide."
            )
            assert written["key_points"] == ["Uses light", "Makes sugar"]
            assert written["grading_notes"] == "Accept glucose for sugar."
            assert written["explanation"] == "Light energy supports sugar production."
            assert [
                (row["text"], row["is_correct"], row["order_index"])
                for row in multiple_choice["choices"]
            ] == [
                ("Respiration", False, 1),
                ("Photosynthesis", True, 2),
                ("Digestion", False, 3),
                ("Evaporation", False, 4),
            ]
            assert all(isinstance(row["id"], int) for row in multiple_choice["choices"])
            assert multiple_choice["source_reference"] == "science/chapter-1#plants"
            assert multiple_choice["source_excerpt"] == "Plants use light to make sugar."
            assert multiple_choice["explanation"] == "Photosynthesis makes sugar."

    asyncio.run(exercise())


def test_update_draft_question_replaces_choices_and_rubric(db_session):
    quiz_id, choice_id, written_id = seed_draft_exam(db_session)

    async def exercise():
        async with Client(build_server()) as client:
            updated = (
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": choice_id,
                        "content": {
                            "question_type": "multiple_choice",
                            "stem": "What powers photosynthesis?",
                            "explanation": "Sunlight supplies energy.",
                            "choices": replacement_choices(),
                        },
                    },
                )
            ).data["question"]
            assert updated["stem"] == "What powers photosynthesis?"
            assert updated["explanation"] == "Sunlight supplies energy."
            assert [row["text"] for row in updated["choices"]] == [
                "Sunlight",
                "Sand",
                "Salt",
                "Smoke",
            ]
            assert [row["is_correct"] for row in updated["choices"]] == [
                True,
                False,
                False,
                False,
            ]
            assert updated["source_reference"] == "science/chapter-1#plants"
            retained = (
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": choice_id,
                        "content": {
                            "question_type": "multiple_choice",
                            "stem": "What powers photosynthesis?",
                            "choices": replacement_choices(),
                        },
                    },
                )
            ).data["question"]
            assert retained["explanation"] == "Sunlight supplies energy."
            rubric = (
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": written_id,
                        "content": {
                            "question_type": "open_ended",
                            "stem": "Describe how plants make sugar.",
                            "explanation": None,
                            "model_answer": "Plants use sunlight to make sugar.",
                            "key_points": ["Uses sunlight", "Produces sugar"],
                            "grading_notes": "Accept glucose.",
                        },
                    },
                )
            ).data["question"]
            assert rubric["stem"] == "Describe how plants make sugar."
            assert rubric["explanation"] is None
            assert rubric["model_answer"] == "Plants use sunlight to make sugar."
            assert rubric["key_points"] == ["Uses sunlight", "Produces sugar"]
            assert rubric["grading_notes"] == "Accept glucose."
            retained_notes = (
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": written_id,
                        "content": {
                            "question_type": "open_ended",
                            "stem": "Describe how plants make sugar.",
                            "model_answer": "Plants use sunlight to make sugar.",
                            "key_points": ["Uses sunlight", "Produces sugar"],
                        },
                    },
                )
            ).data["question"]
            assert retained_notes["grading_notes"] == "Accept glucose."
            with pytest.raises(ToolError):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": written_id,
                        "content": {
                            "question_type": "open_ended",
                            "stem": "Incomplete rubric",
                            "model_answer": "An answer",
                        },
                    },
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": choice_id,
                        "content": {
                            "question_type": "multiple_choice",
                            "stem": "Invalid key",
                            "choices": [
                                {"text": f"Option {index}", "is_correct": False}
                                for index in range(4)
                            ],
                        },
                    },
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": written_id,
                        "content": {
                            "question_type": "multiple_choice",
                            "stem": "Wrong type",
                            "choices": replacement_choices(),
                        },
                    },
                )

    asyncio.run(exercise())
    db_session.expire_all()
    assert db_session.get(Question, choice_id).stem == "What powers photosynthesis?"
    assert db_session.get(Question, written_id).model_answer == "Plants use sunlight to make sugar."


def test_update_refuses_attempted_published_and_cross_workspace_questions(db_session):
    quiz_id, choice_id, written_id = seed_draft_exam(db_session)
    attempted = Attempt(quiz_id=quiz_id, score=0, total_questions=1)
    attempted.answers = [AttemptAnswer(question_id=choice_id, score=0, is_correct=False)]
    db_session.add(attempted)
    db_session.commit()
    learner_id = create_learner("readback-child@example.com", "Readback child")
    shared = Quiz(
        title="Published reuse",
        quiz_type=QuizType.EXAM,
        status=QuizStatus.PUBLISHED,
        question_links=[QuizQuestion(question=db_session.get(Question, written_id), order_index=1)],
    )
    db_session.add(shared)
    db_session.commit()

    async def exercise():
        async with Client(build_server()) as client:
            with pytest.raises(ToolError, match="attempts"):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": choice_id,
                        "content": {
                            "question_type": "multiple_choice",
                            "stem": "Blocked attempt",
                            "choices": replacement_choices(),
                        },
                    },
                )
            with pytest.raises(ToolError, match="every linked quiz"):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": quiz_id,
                        "question_id": written_id,
                        "content": {
                            "question_type": "open_ended",
                            "stem": "Blocked publication",
                            "model_answer": "A model answer",
                            "key_points": ["First", "Second"],
                        },
                    },
                )
            with pytest.raises(ToolError, match="draft quiz"):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": 1,
                        "quiz_id": shared.id,
                        "question_id": written_id,
                        "content": {
                            "question_type": "open_ended",
                            "stem": "Blocked published quiz",
                            "model_answer": "A model answer",
                            "key_points": ["First", "Second"],
                        },
                    },
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "get_quiz",
                    {"workspace_id": learner_id, "quiz_id": quiz_id, "include_answers": True},
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "update_draft_question",
                    {
                        "workspace_id": learner_id,
                        "quiz_id": quiz_id,
                        "question_id": choice_id,
                        "content": {
                            "question_type": "multiple_choice",
                            "stem": "Cross workspace",
                            "choices": replacement_choices(),
                        },
                    },
                )

    asyncio.run(exercise())
    db_session.expire_all()
    assert db_session.get(Question, choice_id).stem == "Which process makes sugar?"
    assert db_session.get(Question, written_id).stem == "Explain photosynthesis."


@pytest.mark.parametrize("conflict", ["publish", "attempt"])
def test_draft_update_waits_for_publication_or_attempt(db_session, conflict):
    quiz_id, choice_id, _ = seed_draft_exam(db_session)
    access = db_session.access
    started = Event()

    def edit_question():
        started.set()
        with workspace_session(access, drafts_only=True) as db:
            return update_draft_question(
                db,
                quiz_id,
                choice_id,
                DraftMultipleChoiceEdit(
                    stem="Changed after competing write",
                    choices=[ChoiceCreate(**choice) for choice in replacement_choices()],
                ),
            )

    with get_session_factory()() as competing:
        quiz = competing.scalar(select(Quiz).where(Quiz.id == quiz_id).with_for_update())
        assert quiz is not None
        if conflict == "publish":
            quiz.status = QuizStatus.PUBLISHED
        else:
            attempt = Attempt(quiz_id=quiz_id, workspace_id=access.workspace_id, total_questions=1)
            attempt.answers = [
                AttemptAnswer(
                    question_id=choice_id,
                    workspace_id=access.workspace_id,
                    score=0,
                    is_correct=False,
                )
            ]
            competing.add(attempt)
        competing.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit_question)
            assert started.wait(timeout=5)
            try:
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.2)
            finally:
                competing.commit()
            with pytest.raises(ValueError, match="draft quiz|attempts"):
                future.result(timeout=5)

    db_session.expire_all()
    assert db_session.get(Question, choice_id).stem == "Which process makes sugar?"


def test_learner_take_page_hides_correct_flags_and_explanation(db_session):
    learner_id = create_learner("readback-learner@example.com", "Readback learner")
    with learner_session(learner_id) as db:
        subject = Subject(name="Learner science", slug="learner-science")
        quiz = Quiz(
            title="Learner quiz",
            primary_subject=subject,
            status=QuizStatus.PUBLISHED,
            question_links=[
                QuizQuestion(
                    question=Question(
                        subject=subject,
                        stem="What makes sugar?",
                        explanation="SECRET EXPLANATION: photosynthesis.",
                        choices=[
                            Choice(text="Photosynthesis", is_correct=True, order_index=1),
                            Choice(text="Respiration", is_correct=False, order_index=2),
                            Choice(text="Digestion", is_correct=False, order_index=3),
                            Choice(text="Evaporation", is_correct=False, order_index=4),
                        ],
                    ),
                    order_index=1,
                )
            ],
        )
        db.add(quiz)
        db.commit()
        quiz_id = quiz.id
        question_id = quiz.questions[0].id
        subject_id = subject.id

    browser = learner_browser(learner_id)
    response = browser.get(f"/quizzes/{quiz_id}/take")

    assert response.status_code == 200
    assert "Photosynthesis" in response.text
    assert "Respiration" in response.text
    assert "is_correct" not in response.text
    assert "SECRET EXPLANATION" not in response.text
    assert browser.get(f"/quizzes/{quiz_id}").status_code == 303
    question_form = browser.get(f"/quizzes/{quiz_id}/questions/new")
    assert question_form.status_code == 200
    assert "choice-chip-correct" not in question_form.text
    assert "SECRET EXPLANATION" not in question_form.text
    assert browser.get(f"/quizzes/{quiz_id}/questions/{question_id}/edit").status_code == 403
    assert browser.post(f"/quizzes/{quiz_id}/questions/{question_id}", data={}).status_code == 403
    subject_response = browser.get(f"/subjects/{subject_id}")
    assert subject_response.status_code == 200
    assert "SECRET EXPLANATION" not in subject_response.text
    assert "choice-chip-correct" not in subject_response.text
    for path in ("/api/quizzes", f"/api/quizzes/{quiz_id}"):
        api_response = browser.get(path)
        assert api_response.status_code == 200
        assert "Photosynthesis" in api_response.text
        assert "is_correct" not in api_response.text
        assert "SECRET EXPLANATION" not in api_response.text
    reviewer_response = reviewer_browser(learner_id).get(f"/api/quizzes/{quiz_id}")
    assert reviewer_response.status_code == 200
    assert reviewer_response.json()["questions"][0]["choices"][0]["is_correct"] is True
    assert "SECRET EXPLANATION" in reviewer_response.text
    draft = browser.post(
        "/api/quizzes", json={"title": "Learner draft", "topic": "Learner science"}
    )
    assert draft.status_code == 201
    created = browser.post(
        f"/api/quizzes/{draft.json()['id']}/questions",
        json={
            "stem": "Which process makes sugar?",
            "explanation": "A hidden draft explanation.",
            "choices": replacement_choices(),
            "concept_proposals": [{"name": "Photosynthesis", "role": "primary"}],
        },
    )
    assert created.status_code == 201, created.text
    assert "is_correct" not in created.text
    assert "A hidden draft explanation" not in created.text
