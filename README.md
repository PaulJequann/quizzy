<div align="center">
  <img src="app/static/icons/icon-192.png" alt="Quizzy logo" width="96" height="96">
  <h1>Quizzy</h1>
  <p><strong>Turn study notes into grounded quizzes, focused practice, and concept-level mastery.</strong></p>
  <p>
    Quizzy is a self-hosted FastAPI study app with an MCP workflow for generating durable
    question banks from your own notes. Bring local models, hosted model subscriptions, or any
    MCP-capable AI client; Quizzy stores the learning system.
  </p>

  <p>
    <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white">
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi&logoColor=white">
    <img alt="Postgres" src="https://img.shields.io/badge/Postgres-16-4169E1?style=flat-square&logo=postgresql&logoColor=white">
    <img alt="MCP ready" src="https://img.shields.io/badge/MCP-ready-111827?style=flat-square">
    <img alt="PWA ready" src="https://img.shields.io/badge/PWA-ready-0f766e?style=flat-square">
    <a href="https://github.com/PaulJequann/quizzy/actions/workflows/cicd.yaml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/PaulJequann/quizzy/cicd.yaml?branch=main&label=CI&style=flat-square"></a>
  </p>

  <p>
    <a href="#why-quizzy">Why Quizzy</a> ·
    <a href="#screenshots">Screenshots</a> ·
    <a href="#quickstart">Quickstart</a> ·
    <a href="#mcp-workflow">MCP Workflow</a> ·
    <a href="#deploy">Deploy</a>
  </p>
</div>

<p align="center">
  <img src="docs/readme/quizzy-practice-builder.jpg" alt="Quizzy practice builder" width="900">
</p>

## Why Quizzy

Most AI quiz tools stop at one-off generation. Quizzy is built around the part that matters after
the first prompt: keeping a reusable question bank, preserving source grounding, maintaining a
subject concept map, and using quiz attempts to decide what to review next.

Use it when you want to:

- Convert Markdown study notes into multiple-choice questions through an MCP-enabled agent.
- Keep generated questions grounded to a source document, source excerpt, or explicit reference.
- Organize knowledge by subject and durable learning concepts instead of loose tags.
- Build quick practice, focused concept drills, and mastery review sessions from the same bank.
- Review concepts, question quality, and generated taxonomy before they pollute practice.
- Install the app as a mobile-friendly PWA for daily study sessions.

> [!IMPORTANT]
> Quizzy does not call an LLM directly. Your MCP client or agent reads notes, decides what to
> generate, and writes structured questions into Quizzy. That keeps model choice outside the app:
> local models, paid subscriptions, hosted tools, and future MCP clients can all use the same bank.

## Screenshots

| Mastery command center | Results review |
| --- | --- |
| <img src="docs/readme/quizzy-mastery.jpg" alt="Quizzy mastery dashboard" width="520"> | <img src="docs/readme/quizzy-results.jpg" alt="Quizzy quiz results review" width="520"> |

| Quiz library | Mobile practice builder |
| --- | --- |
| <img src="docs/readme/quizzy-quiz-library.jpg" alt="Quizzy quiz library" width="520"> | <img src="docs/readme/quizzy-mobile-practice.jpg" alt="Quizzy mobile practice builder" width="260"> |

## Features

- **Source-grounded generation**: ingest Markdown notes as source documents with snapshots,
  hashes, display paths, and optional Obsidian URIs.
- **MCP-first AI workflow**: expose tools for source ingestion, generation context, concept
  planning, durable question creation, quiz assembly, and mastery focus.
- **Reusable question bank**: store questions independently from quizzes, then assemble standard,
  generated, practice, and review quizzes.
- **Concept mastery**: track subject-scoped concepts, weak/improving/strong status, attempts,
  accuracy, missed dates, and review queues.
- **Human-in-the-loop stewardship**: save proposed concepts and unresolved assignments for review
  before they become active study material.
- **Focused practice**: build sessions by subject, concept selection, question count, and
  difficulty.
- **Review-rich results**: compare selected and correct answers, read explanations, flag question
  issues, and record retrieval friction after seeing each answer.
- **Installable PWA shell**: web app manifest, app icons, root service worker, offline page, mobile
  navigation, and responsive templates.
- **Production-minded backend**: FastAPI, Jinja, HTMX, SQLAlchemy, Alembic, Postgres, Docker,
  Testcontainers-backed integration tests, Ruff, ty, and pytest.

## How It Works

```mermaid
flowchart LR
  A["Markdown notes"] --> B["MCP client / AI agent"]
  B --> C["Quizzy MCP tools"]
  C --> D["Source documents + snapshots"]
  C --> E["Generated question bank"]
  E --> F["Practice and review quizzes"]
  F --> G["Attempts + explanations"]
  G --> H["Concept mastery queues"]
  H --> F
```

The agent does the reading and inference. Quizzy provides the storage model, validation rules, UI,
and review loops that turn generated questions into a maintainable study system.

## Quickstart

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker, for local Postgres

### Run Locally

```bash
uv sync
cp .env.example .env
docker compose up -d db
uv run alembic upgrade head
uv run python -m app.auth.provision --parent-email parent@example.com --learner-email learner@example.com --learner-name Learner
# Set MCP_ADMIN_USER_ID in .env to the printed parent workspace ID.
uv run python -m scripts.seed --workspace-id 1
uv run uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Sign in with the credentials you chose during provisioning. Use the actual workspace IDs printed
by that command; the example seed destination is `1`. See [Study account setup](docs/family-accounts.md)
for private libraries, learner publication, and MCP configuration.

The seed command creates AWS, Linux, and Python study material in the selected workspace.
The default `.env.example` points host-side tools at the Compose database on port `55432`.

### Run With Docker Compose

```bash
docker compose up --build
```

In another terminal, initialize the database once:

```bash
docker compose exec web uv run alembic upgrade head
docker compose exec web uv run python -m app.auth.provision --parent-email parent@example.com --learner-email learner@example.com --learner-name Learner
# Set MCP_ADMIN_USER_ID, then recreate web to load its updated environment.
docker compose exec web uv run python -m scripts.seed --workspace-id 1
```

## MCP Workflow

Quizzy ships with a local stdio MCP server:

```bash
uv run python mcp_server.py
```

This repo also includes a project-scoped Codex config at `.codex/config.toml`:

```toml
[mcp_servers.quizzy]
command = "uv"
args = ["run", "python", "mcp_server.py"]
startup_timeout_sec = 20
tool_timeout_sec = 120
default_tools_approval_mode = "prompt"
```

Core MCP tools include:

- `list_workspaces` (no workspace_id required; identifies the configured administrator)
- `upsert_source_document_from_content`
- `prepare_generation_context`
- `prepare_concept_plan`
- `create_generated_questions`
- `create_generated_quiz`
- `create_generated_quiz_with_questions`
- `list_question_bank`
- `get_quiz` (`include_answers=true` for ordered choices, correct flags, and explanations)
- `update_draft_question` (replace a question before publication or attempts)
- `archive_question` (retire a question; practice and concept reviews skip it)
- `get_mastery_focus`
- `get_subject_mastery_tree`

Recommended generation flow:

0. When the target workspace is not stated, call `list_workspaces` and select the
   learner's workspace by name instead of asking for an internal workspace id.
1. Ingest the note content into Quizzy as a source document.
2. Ask Quizzy for existing subject, concept, and generation context.
3. Have the agent create a small concept plan before writing questions.
4. Persist source-grounded questions. A multiple-choice question has exactly four choices and
   one correct answer. Open-ended questions belong to exams only: pass `exam=true` when creating
   the quiz. See [Study account setup](docs/family-accounts.md) for exam authoring and grading.
5. Review proposed concepts or unresolved assignments in the subject workspace.
6. Call `get_quiz` with `include_answers=true` to verify each stored answer key and explanation.
   It also returns source references and excerpts, and open-ended model answers, key points,
   and grading notes. This read is available only through the administrator MCP server and
   requires the target `workspace_id`.
7. If a draft needs correction, call `update_draft_question` with `quiz_id`, `question_id`, and
   a `content` object containing the question type, replacement `stem`, and either four ordered
   `choices` (each with `text` and `is_correct`) or the open-ended `model_answer`, `key_points`,
   and optional `grading_notes`. Omit `explanation` to keep it, or pass a string or `null` to
   replace or clear it. Omitted `grading_notes` also keep their stored value; pass `null` to
   clear them. The question must belong only to draft quizzes and have no attempts;
   its type and source provenance stay unchanged. Publication remains a browser action.
   A question that is published or already attempted cannot be edited. Call
   `archive_question` to retire it, then create a corrected question as a new draft.
8. Assemble practice or review quizzes from the durable question bank.

Learner quiz API responses contain stems and choice text, without correctness flags,
explanations, or grading rubrics. The full answer-key response is limited to the
configured administrator's MCP access and administrator quiz views and API sessions.
Learners can still create private questions; editing an existing answer key requires an
administrator session.

## Deploy

Build and run the web app from the included Dockerfile:

```bash
docker build -t quizzy .

docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="postgresql+psycopg://quizzy:quizzy@host.docker.internal:55432/quizzy" \
  quizzy uv run --no-sync alembic upgrade head

docker run --rm -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="postgresql+psycopg://quizzy:quizzy@host.docker.internal:55432/quizzy" \
  -e APP_ENV=production \
  -e APP_BASE_URL="http://127.0.0.1:8000" \
  -e SECRET_KEY="change-me" \
  quizzy
```

Run Alembic migrations as a release step before serving traffic. In a real deployment, replace the
database URL with a managed or private Postgres address reachable from the container.

For hosted MCP, run the HTTP MCP app as a separate process or service from the same image:

```bash
uvicorn app.mcp_http:app --host 0.0.0.0 --port 8008
```

Hosted MCP settings:

```text
DATABASE_URL=postgresql+psycopg://...
APP_BASE_URL=https://quizzy.example.com
MCP_AUTH_ENABLED=true
MCP_BEARER_TOKEN=...  # Required for hosted MCP
MCP_ADMIN_USER_ID=1  # Use your provisioned parent ID
MCP_ALLOWED_ORIGINS=https://quizzy.example.com
MCP_MAX_REQUEST_BYTES=2000000
```

Remote MCP clients connect to:

```text
https://quizzy.example.com/mcp
Authorization: Bearer <MCP_BEARER_TOKEN>
```

Hosted MCP requires bearer authentication and an active administrator selected by
`MCP_ADMIN_USER_ID`. It refuses to start with `MCP_AUTH_ENABLED=false`. Local stdio trusts
access to the process but still requires the configured administrator. Every MCP tool call
includes an explicit `workspace_id`. Generated and assembled quizzes stay draft until the
parent publishes them in the browser.

The web app uses local accounts with private workspaces. See [Study account setup](docs/family-accounts.md)
for migration, passwords, learner access, and reviewer oversight.

## Development

Useful commands:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
make test-unit
make test-db
make test
make test-final
```

Database-backed API, MCP, integration, and migration tests use Testcontainers Postgres and Alembic
migrations. SQLite is not used as proof that the app works.

## Project Map

```text
app/
  mcp/          FastMCP tools for source ingestion, generation, and mastery reads
  routes/       FastAPI web, API, scheduler, and PWA routes
  services/     Domain logic for quizzes, concepts, mastery, generation, and sources
  templates/    Jinja + HTMX web UI
  static/       CSS, PWA manifest, service worker, icons, offline page
alembic/        Postgres schema migrations
scripts/        Seed data
tests/          Unit, API, integration, MCP, PWA, and migration tests
```
