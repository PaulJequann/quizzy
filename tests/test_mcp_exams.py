import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import func, select

from app.mcp.server import build_server
from app.models import Question
from tests.db_utils import clear_database_caches, reset_database
from tests.db_utils import get_test_session_factory as get_session_factory

pytestmark = pytest.mark.mcp


@pytest.fixture
def mcp_database(test_database_url, monkeypatch):
    clear_database_caches()
    for session in reset_database(test_database_url, monkeypatch):
        session.close()
        try:
            yield
        finally:
            clear_database_caches()


async def _invoke(server, name: str, args: dict):
    result = await server.call_tool(name, {"workspace_id": 1, **args})
    return result.structured_content


async def _approved_invoke(server, name: str, args: dict):
    async with Client(server) as client:
        result = await client.call_tool(name, {"workspace_id": 1, **args})
        return result.data


PHOTOSYNTHESIS_CONCEPT = [
    {
        "name": "Photosynthesis",
        "role": "primary",
        "rationale": "Reusable plant biology learning objective.",
    }
]
OPEN_ENDED_GENERATED = {
    "question_type": "open_ended",
    "stem": "Explain how a plant makes its food.",
    "model_answer": "Plants turn light, water, and carbon dioxide into sugar.",
    "key_points": ["Uses light energy", "Makes sugar"],
    "grading_notes": "Accept glucose for sugar.",
    "concept_proposals": PHOTOSYNTHESIS_CONCEPT,
    "source_reference": "biology/plants.md#photosynthesis",
}
MULTIPLE_CHOICE_GENERATED = {
    "stem": "Which gas do plants absorb for photosynthesis?",
    "choices": [
        {"text": "Carbon dioxide", "is_correct": True},
        {"text": "Oxygen, because plants breathe it in to make food"},
        {"text": "Nitrogen, because it makes up most of the air"},
        {"text": "Helium, because it is light"},
    ],
    "concept_proposals": PHOTOSYNTHESIS_CONCEPT,
    "source_reference": "biology/plants.md#photosynthesis",
}


def test_mcp_creates_exam_quiz_with_open_ended_question(mcp_database):
    server = build_server()

    created = asyncio.run(
        _approved_invoke(
            server,
            "create_generated_quiz_with_questions",
            {
                "title": "Plant exam",
                "subject_name": "Biology",
                "exam": True,
                "questions": [OPEN_ENDED_GENERATED, MULTIPLE_CHOICE_GENERATED],
            },
        )
    )

    assert created["quiz_type"] == "exam"
    open_ended, multiple_choice = created["questions"]
    assert (open_ended["question_type"], open_ended["exam_only"]) == ("open_ended", True)
    assert open_ended["model_answer"] == "Plants turn light, water, and carbon dioxide into sugar."
    assert open_ended["grading_notes"] == "Accept glucose for sugar."
    assert open_ended["key_points"] == ["Uses light energy", "Makes sugar"]
    assert (multiple_choice["question_type"], multiple_choice["exam_only"]) == (
        "multiple_choice",
        True,
    )


def test_mcp_refuses_open_ended_question_outside_exam_quiz(mcp_database):
    server = build_server()

    with pytest.raises(ToolError, match="Open-ended questions belong only in exam quizzes."):
        asyncio.run(
            _approved_invoke(
                server,
                "create_generated_quiz_with_questions",
                {
                    "title": "Plant practice",
                    "subject_name": "Biology",
                    "questions": [OPEN_ENDED_GENERATED],
                },
            )
        )
    quiz = asyncio.run(
        _invoke(server, "create_generated_quiz", {"title": "Draft", "subject_name": "Biology"})
    )
    with pytest.raises(ToolError, match="Open-ended questions belong only in exam quizzes."):
        asyncio.run(
            _approved_invoke(
                server,
                "add_questions_to_quiz",
                {"quiz_id": quiz["id"], "questions": [OPEN_ENDED_GENERATED]},
            )
        )
    with pytest.raises(ToolError, match="key_points"):
        asyncio.run(
            _approved_invoke(
                server,
                "create_generated_quiz_with_questions",
                {
                    "title": "Mixed",
                    "subject_name": "Biology",
                    "exam": True,
                    "questions": [{**MULTIPLE_CHOICE_GENERATED, "key_points": ["Light"]}],
                },
            )
        )

    with get_session_factory()() as db:
        assert db.scalar(select(func.count(Question.id))) == 0
