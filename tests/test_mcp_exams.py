import asyncio
import os
from datetime import datetime
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from sqlalchemy import func, select

from app.mcp.server import build_server
from app.models import (
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Question,
    QuestionConcept,
    QuestionType,
)
from app.services.concept_service import normalize_concept_name
from tests.db_utils import clear_database_caches, reset_database
from tests.db_utils import get_test_session_factory as get_session_factory
from tests.test_open_ended_exam_grading import (
    GRADING_NOTES,
    KEY_POINTS,
    LEARNER_ANSWER,
    MODEL_ANSWER,
    RELEASE_FORM,
    WRITTEN_EXPLANATION,
    WRITTEN_STEM,
    stored_grades,
    submitted_exam,
)
from tests.workspace_clients import PARENT_ID, reviewer_session

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


SOURCE_EXCERPT = "Leaves capture light and make sugar."
AGENT_GRADE = {
    "question_key": 2,
    "key_points_met": [2, 1],
    "feedback": "  You named light and sugar. Add what the plant gives off.  ",
    "misconception": "thinks plants eat soil",
}
RECORDED_GRADE = {
    "question_key": 2,
    "key_points_met": [1, 2],
    "feedback": "You named light and sugar. Add what the plant gives off.",
    "misconception": "thinks plants eat soil",
}


def link_written_question(learner_id: int) -> tuple[int, int]:
    with reviewer_session(learner_id) as db:
        question = db.scalar(
            select(Question).where(Question.question_type == QuestionType.OPEN_ENDED)
        )
        assert question is not None
        question.source_reference = "biology/plants.md#photosynthesis"
        question.source_excerpt = SOURCE_EXCERPT
        concept = Concept(
            name="Photosynthesis",
            slug="photosynthesis",
            normalized_name=normalize_concept_name("Photosynthesis", subject_name="Biology"),
            status=ConceptStatus.ACTIVE,
            subject=question.subject,
        )
        question.concept_links = [
            QuestionConcept(concept=concept, role=ConceptAssignmentRole.PRIMARY)
        ]
        db.commit()
        return question.id, concept.id


async def _grade_over_stdio(learner_id: int, exam_id: int) -> list[dict]:
    transport = StdioTransport(
        command="uv",
        args=["run", "python", "mcp_server.py"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    calls = [
        ("list_pending_exam_submissions", {}),
        ("get_exam_submission_for_grading", {"exam_id": exam_id}),
        (
            "record_exam_grades",
            {"exam_id": exam_id, "grades": [AGENT_GRADE], "review_note": "Draft."},
        ),
        (
            "record_exam_grades",
            {"exam_id": exam_id, "grades": [AGENT_GRADE], "review_note": "Draft."},
        ),
        ("list_pending_exam_submissions", {}),
    ]
    async with Client(transport) as client:
        return [
            (await client.call_tool(name, {"workspace_id": learner_id, **args})).structured_content
            for name, args in calls
        ]


async def _grading_call(server, workspace_id: int, name: str, args: dict):
    async with Client(server) as client:
        result = await client.call_tool(name, {"workspace_id": workspace_id, **args})
        return result.structured_content


def test_mcp_grading_flow_in_fresh_process(mcp_database):
    exam = submitted_exam()
    question_id, concept_id = link_written_question(exam.learner_id)

    pending, packet, recorded, rerecorded, graded = asyncio.run(
        _grade_over_stdio(exam.learner_id, exam.exam_id)
    )

    submitted_at = pending["submissions"][0].pop("submitted_at")
    assert datetime.fromisoformat(submitted_at).tzinfo is not None
    assert pending == {
        "submissions": [
            {
                "exam_id": exam.exam_id,
                "title": "Plant exam",
                "open_ended_count": 1,
                "ungraded_count": 1,
                "actor_id": exam.learner_id,
                "actor_name": "Child",
                "actor_role": "learner",
            }
        ]
    }
    assert packet["questions"][1] == {
        "type": "open_ended",
        "key": 2,
        "question_id": question_id,
        "stem": WRITTEN_STEM,
        "explanation": WRITTEN_EXPLANATION,
        "model_answer": MODEL_ANSWER,
        "grading_notes": GRADING_NOTES,
        "key_points": KEY_POINTS,
        "answer": LEARNER_ANSWER,
        "grade": None,
        "source_reference": "biology/plants.md#photosynthesis",
        "source_excerpt": SOURCE_EXCERPT,
        "concepts": [{"concept_id": concept_id, "name": "Photosynthesis", "role": "primary"}],
    }
    assert (packet["questions"][0]["type"], packet["questions"][0]["answer"]) == (
        "multiple_choice",
        1,
    )
    assert packet["mastery"] == [
        {
            "concept_id": concept_id,
            "name": "Photosynthesis",
            "mastery_state": "unseen",
            "status": "weak",
            "accuracy": 0.0,
            "total_attempts": 0,
            "correct_attempts": 0,
        }
    ]
    expected = {"exam_id": exam.exam_id, "review_note": "Draft.", "grades": [RECORDED_GRADE]}
    assert recorded == expected
    assert rerecorded == expected
    assert graded["submissions"][0]["ungraded_count"] == 0
    assert stored_grades(exam.learner_id) == [
        (2, [1, 2], RECORDED_GRADE["feedback"], "thinks plants eat soil")
    ]


def test_mcp_grading_refusals(mcp_database):
    exam = submitted_exam()
    server = build_server()

    def call(name: str, args: dict, workspace_id: int = exam.learner_id):
        return asyncio.run(_grading_call(server, workspace_id, name, args))

    record = {"exam_id": exam.exam_id, "review_note": "Draft."}
    assert call("record_exam_grades", {**record, "grades": []}) == {
        "exam_id": exam.exam_id,
        "review_note": "Draft.",
        "grades": [],
    }
    with pytest.raises(ToolError, match="Grades apply only to open-ended questions in this exam"):
        call("record_exam_grades", {**record, "grades": [{**AGENT_GRADE, "question_key": 1}]})
    with pytest.raises(ToolError, match="Grade each question at most once"):
        call("record_exam_grades", {**record, "grades": [AGENT_GRADE, AGENT_GRADE]})
    with pytest.raises(ToolError, match="Only a reviewer previewing a learner can grade"):
        call("list_pending_exam_submissions", {}, workspace_id=PARENT_ID)

    call("record_exam_grades", {**record, "grades": [AGENT_GRADE]})
    release = exam.reviewer.post(f"/exams/{exam.exam_id}/release", data=RELEASE_FORM)
    assert release.status_code == 303

    with pytest.raises(ToolError, match="Released exam results cannot change"):
        call("record_exam_grades", {**record, "grades": [AGENT_GRADE]})
    with pytest.raises(ToolError, match="This exam result is already released"):
        call("get_exam_submission_for_grading", {"exam_id": exam.exam_id})
    assert call("list_pending_exam_submissions", {}) == {"submissions": []}
    assert stored_grades(exam.learner_id) == [(2, [1, 2], "You named light and sugar.", None)]
