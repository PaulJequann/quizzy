#!/usr/bin/env python3
import argparse
import json
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from testcontainers.postgres import PostgresContainer


def seed_family() -> dict:
    from sqlalchemy import select

    from app.auth.models import User
    from app.auth.provision import FamilySetup, provision_family
    from app.database import get_session_factory
    from app.models import Quiz, QuizStatus
    from app.study_access import resolve_study_access
    from app.workspace_database import workspace_session
    from tests.conftest import seed_test_quiz

    parent, learner = provision_family(
        FamilySetup(
            "parent@example.com",
            "Verify-Parent-2026",
            "learner@example.com",
            "Verify-Learner-2026",
            "Learner",
        )
    )
    quizzes = {}
    with get_session_factory()() as accounts:
        for account_id, title in ((parent, "Parent private study"), (learner, "Learner practice")):
            account = accounts.get(User, account_id)
            assert account is not None
            with workspace_session(resolve_study_access(accounts, account)) as study:
                seed_test_quiz(study)
                quiz = study.scalar(select(Quiz))
                assert quiz is not None
                quiz.title = title
                draft = Quiz(title=title + " unpublished draft", status=QuizStatus.DRAFT)
                study.add(draft)
                study.commit()
                quizzes[str(account_id)] = {"published": quiz.id, "draft": draft.id}
    return {"parent_workspace": parent, "learner_workspace": learner, "quizzes": quizzes}


def wait_ready(server: subprocess.Popen, port: int, evidence: Path) -> None:
    for _ in range(100):
        if server.poll() is not None:
            raise RuntimeError("App exited; inspect server.log")
        try:
            with urlopen(f"http://127.0.0.1:{port}/login", timeout=1) as response:
                if response.status == 200:
                    (evidence / "ready").write_text(str(server.pid))
                    print(f"READY http://127.0.0.1:{port} evidence={evidence}", flush=True)
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("App readiness timed out")


def drive_server(port: int, evidence: Path, stop: threading.Event) -> None:
    with (evidence / "server.log").open("w") as log:
        server = subprocess.Popen(
            [
                "uv",
                "run",
                "--no-sync",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            wait_ready(server, port, evidence)
            while not stop.wait(0.5):
                if server.poll() is not None:
                    raise RuntimeError("App exited during verification")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8022)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    args.evidence.mkdir(parents=True, exist_ok=False)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        os.environ.update(
            DATABASE_URL=postgres.get_connection_url(),
            APP_BASE_URL=f"http://127.0.0.1:{args.port}",
            APP_ENV="development",
            SECRET_KEY="disposable-verification-only",
            MCP_ADMIN_USER_ID="1",
        )
        from alembic.config import Config

        from alembic import command

        command.upgrade(Config("alembic.ini"), "head")
        manifest = seed_family()
        manifest.update(
            launcher_pid=os.getpid(),
            url=os.environ["APP_BASE_URL"],
            container_id=postgres.get_wrapped_container().id,
            revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        )
        (args.evidence / "instance.json").write_text(json.dumps(manifest, indent=2))
        try:
            drive_server(args.port, args.evidence, stop)
        finally:
            (args.evidence / "stopped").write_text("App stopped; container cleanup follows.\n")
    (args.evidence / "cleaned").write_text("App and disposable Postgres removed.\n")


if __name__ == "__main__":
    main()
