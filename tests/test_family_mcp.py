import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from app.mcp.server import build_server

pytestmark = pytest.mark.mcp


def test_mcp_requires_explicit_workspace_and_active_admin(db_session, monkeypatch):
    async def exercise():
        async with Client(build_server()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("list_subjects", {})
            result = await client.call_tool("list_subjects", {"workspace_id": 1})
            assert "testing" in str(result.data)
            monkeypatch.setenv("MCP_ADMIN_USER_ID", "999")
            from app.config import get_settings

            get_settings.cache_clear()
            with pytest.raises(ToolError):
                await client.call_tool("list_subjects", {"workspace_id": 1})

    asyncio.run(exercise())


def test_list_workspaces_discovers_accessible_workspaces(db_session):
    async def exercise():
        from app.auth.models import User
        from app.database import get_session_factory

        with get_session_factory()() as accounts:
            child = User(
                email="discovery-child@example.com",
                hashed_password="!",
                display_name="Discovery Child",
                is_active=True,
                is_superuser=False,
            )
            inactive = User(
                email="discovery-inactive@example.com",
                hashed_password="!",
                display_name="Discovery Inactive",
                is_active=False,
                is_superuser=False,
            )
            accounts.add_all([child, inactive])
            accounts.commit()

        async with Client(build_server()) as client:
            result = await client.call_tool("list_workspaces", {})
            assert result.data["administrator"] == {"workspace_id": 1, "name": "Parent"}
            workspaces = result.data["workspaces"]
            assert [workspace["workspace_id"] for workspace in workspaces] == [1, child.id]
            assert [workspace["name"] for workspace in workspaces] == [
                "Parent",
                "Discovery Child",
            ]
            assert [workspace["is_admin_workspace"] for workspace in workspaces] == [
                True,
                False,
            ]

    asyncio.run(exercise())


def test_list_workspaces_fails_closed_without_a_configured_admin(db_session, monkeypatch):
    async def exercise():
        async with Client(build_server()) as client:
            monkeypatch.setenv("MCP_ADMIN_USER_ID", "999")
            from app.config import get_settings

            get_settings.cache_clear()
            with pytest.raises(ToolError, match="administrator"):
                await client.call_tool("list_workspaces", {})

    asyncio.run(exercise())


def test_list_workspaces_fails_closed_for_inactive_or_demoted_admin(db_session, monkeypatch):
    async def exercise():
        from app.auth.models import User
        from app.config import get_settings
        from app.database import get_session_factory

        monkeypatch.setenv("MCP_ADMIN_USER_ID", "1")
        get_settings.cache_clear()
        async with Client(build_server()) as client:
            with get_session_factory()() as accounts:
                admin = accounts.get(User, 1)
                assert admin is not None
                admin.is_superuser = False
                accounts.commit()

            with pytest.raises(ToolError, match="administrator"):
                await client.call_tool("list_workspaces", {})

            with get_session_factory()() as accounts:
                admin = accounts.get(User, 1)
                assert admin is not None
                admin.is_superuser = True
                admin.is_active = False
                accounts.commit()

            with pytest.raises(ToolError, match="administrator"):
                await client.call_tool("list_workspaces", {})

    asyncio.run(exercise())


def test_mcp_cannot_publish_generated_quiz(db_session):
    async def exercise():
        async with Client(build_server()) as client:
            with pytest.raises(ToolError, match="draft"):
                await client.call_tool(
                    "create_generated_quiz",
                    {
                        "workspace_id": 1,
                        "title": "No publication",
                        "subject_name": "testing",
                        "status": "published",
                    },
                )

    asyncio.run(exercise())


def test_teaching_brief_is_grounded_in_selected_workspace(db_session):
    async def exercise():
        async with Client(build_server()) as client:
            result = await client.call_tool("prepare_next_practice", {"workspace_id": 1})
            assert result.data["workspace_id"] == 1
            assert result.data["objectives"][0]["name"] == "pytest basics"
            assert result.data["objectives"][0]["evidence"] == "thin"
            assert "draft" in result.data["drafting_guidance"]

    asyncio.run(exercise())


def test_hosted_mcp_refuses_disabled_authentication(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()
    from app.mcp_http import create_app

    monkeypatch.setenv("MCP_AUTH_ENABLED", "false")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="authentication"):
        create_app()


def test_concurrent_mcp_destinations_and_practice_drafts(db_session):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import get_session_factory
    from app.models import Subject
    from app.study_access import resolve_study_access
    from app.workspace_database import workspace_session
    from tests.conftest import seed_test_quiz

    with get_session_factory()() as accounts:
        child = User(
            email="mcp-child@example.com",
            hashed_password="!",
            display_name="Child",
            is_active=True,
            is_superuser=False,
        )
        accounts.add(child)
        accounts.commit()
        access = resolve_study_access(accounts, child)
    with workspace_session(access) as private:
        seed_test_quiz(private)
        subject = private.scalar(select(Subject))
        subject.name = "Child only"
        private.commit()
        subject_id = subject.id

    async def exercise():
        async with Client(build_server()) as client:
            parent, learner = await asyncio.gather(
                client.call_tool("list_subjects", {"workspace_id": 1}),
                client.call_tool("list_subjects", {"workspace_id": child.id}),
            )
            assert "Child only" not in str(parent.data)
            assert "Child only" in str(learner.data)
            practice = await client.call_tool(
                "assemble_practice_session",
                {
                    "workspace_id": child.id,
                    "subject_id": subject_id,
                },
            )
            assert practice.data["status"] == "draft"
            with get_session_factory()() as accounts:
                admin = accounts.get(User, 1)
                assert admin is not None
                admin.is_superuser = False
                accounts.commit()
            with pytest.raises(ToolError, match="administrator"):
                await client.call_tool("list_subjects", {"workspace_id": child.id})

    asyncio.run(exercise())


def test_mcp_cannot_add_questions_to_published_quiz(db_session):
    async def exercise():
        async with Client(build_server()) as client:
            with pytest.raises(ToolError, match="published"):
                await client.call_tool(
                    "add_questions_to_quiz",
                    {
                        "workspace_id": 1,
                        "quiz_id": 1,
                        "questions": [
                            {
                                "stem": "Another test question",
                                "source_reference": "testing/pytest.md",
                                "choices": [
                                    {"text": "Yes", "is_correct": True},
                                    {"text": "No"},
                                    {"text": "Maybe"},
                                    {"text": "Never"},
                                ],
                                "concept_proposals": [{"name": "pytest basics", "role": "primary"}],
                            }
                        ],
                    },
                )

    asyncio.run(exercise())
