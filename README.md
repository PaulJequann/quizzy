# Quizzy

Quizzy is a small FastAPI quiz web app POC. It tracks reusable questions, subject-scoped concepts, quiz attempts, and concept-level mastery. It uses Jinja templates, HTMX, Tailwind CSS v4 in the browser, SQLAlchemy, Alembic, Postgres, uv, Ruff, ty, and pytest.

## Local setup

```bash
uv sync
cp .env.example .env
```

Start Postgres locally or use Docker Compose for the database. The example `.env` points
host-side tools such as Alembic, Uvicorn, and Codex MCP at the Compose database on port
`55432`:

```text
postgresql+psycopg://quizzy:quizzy@localhost:55432/quizzy
```

Inside Docker Compose, the web container uses the internal service address `db:5432`.
The host port can be changed with `POSTGRES_PORT`.

## Run locally

```bash
docker compose up -d db
uv run alembic upgrade head
uv run python -m scripts.seed
uv run uvicorn app.main:app --reload
```

Open `http://localhost:8000`.

## Docker Compose

```bash
docker compose up --build
```

In another terminal, initialize the database:

```bash
docker compose exec web uv run alembic upgrade head
docker compose exec web uv run python -m scripts.seed
```

## Useful commands

```bash
uv sync
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "message"
uv run python -m scripts.seed
uv run uvicorn app.main:app --reload
make test-unit
make test-db
make test
make test-final
uv run ruff check .
uv run ruff format .
uv run ty check
```

Automated DB/API/MCP/migration tests use Testcontainers Postgres and run Alembic migrations
against the disposable test database. They do not wipe the active development Postgres database
from `.env`.

## Agent workflow

Agent operating rules live in `AGENTS.md`, including required validation and CodeScene
code-health checks for touched source files.

## MCP ingestion

Quizzy exposes a local FastMCP server so an MCP client such as Codex or Claude can ingest
Obsidian notes, read them as source documents, and write generated quizzes back into the app.
Quizzy does not call an LLM itself; the MCP client is responsible for inference.

Run migrations first:

```bash
uv run alembic upgrade head
```

Useful environment settings in `.env`:

```text
OBSIDIAN_VAULT_ROOTS=/absolute/path/to/your/vault
```

The generated-quiz default difficulty is stored in the app database as
`app_settings.default_difficulty`, so MCP clients read the current app state on every request
instead of caching the value from process startup. The bootstrap fallback is
`DEFAULT_DIFFICULTY=intermediate` when the database row is missing.

MCP tools can inspect and change the generated-quiz default with `get_generation_settings` and
`set_default_difficulty`. Valid values are `beginner`, `intermediate`, and `advanced`.

`OBSIDIAN_VAULT_ROOTS` is optional. When set, it is a comma-separated allowlist of directories
that MCP note-ingestion tools may read from.

Codex CLI, Codex Desktop, and the Codex IDE extension share MCP settings through
`config.toml`. This repo includes a project-scoped config at `.codex/config.toml`:

```toml
[mcp_servers.quizzy]
command = "uv"
args = ["run", "python", "mcp_server.py"]
startup_timeout_sec = 20
tool_timeout_sec = 120
default_tools_approval_mode = "prompt"
env_vars = ["DATABASE_URL", "OBSIDIAN_VAULT_ROOTS"]
```

Codex only loads project-scoped `.codex/config.toml` files for trusted projects. After opening
this repo in Codex CLI or Codex Desktop, trust the workspace if prompted, then use `/mcp` to
confirm the `quizzy` server is connected.

For a user-level setup that follows you outside this repo, add the same server to
`~/.codex/config.toml` with absolute paths:

```toml
[mcp_servers.quizzy]
command = "uv"
args = [
  "--project",
  "/absolute/path/to/quizzy",
  "run",
  "python",
  "/absolute/path/to/quizzy/mcp_server.py",
]
startup_timeout_sec = 20
tool_timeout_sec = 120
default_tools_approval_mode = "prompt"
env_vars = ["DATABASE_URL", "OBSIDIAN_VAULT_ROOTS"]
```

You can also manage the user-level entry from the Codex CLI:

```bash
codex mcp add quizzy \
  --env DATABASE_URL="$DATABASE_URL" \
  --env OBSIDIAN_VAULT_ROOTS="$OBSIDIAN_VAULT_ROOTS" \
  -- uv --project /absolute/path/to/quizzy run python /absolute/path/to/quizzy/mcp_server.py
```

Primary MCP tools:

- `ingest_obsidian_note` / `ingest_obsidian_directory`
- `list_source_documents`, `find_source_documents`, `get_source_document`
- `list_subjects`, `list_concepts`, `create_subject`
- `get_generation_settings`, `set_default_difficulty`
- `create_generated_quiz`, `add_questions_to_quiz`
- `create_generated_quiz_with_questions`
- `get_generation_guidance`

Recommended agent workflow:

1. Ingest relevant Obsidian notes.
2. Read source documents and existing subject/concept context.
3. Decide subject, concepts, difficulty, question set, and source references in the MCP client.
4. Write a draft generated quiz.

Generated questions must have exactly four choices, exactly one correct answer, and source
grounding. Distractors should represent common misconceptions or plausible adjacent mistakes
about the same concept, not random false statements or unrelated options.

## Routes

- `GET /` redirects to `/quizzes`
- `GET /subjects`
- `GET /subjects/{subject_id}`
- `POST /subjects/{subject_id}/review`
- `GET /quizzes`
- `GET /mastery`
- `POST /mastery/review`
- `GET /quizzes/new`
- `POST /quizzes`
- `GET /quizzes/{quiz_id}`
- `GET /quizzes/{quiz_id}/questions/new`
- `POST /quizzes/{quiz_id}/questions`
- `GET /quizzes/{quiz_id}/take`
- `POST /quizzes/{quiz_id}/submit`
- `GET /attempts/{attempt_id}/results`

Minimal JSON routes are available under `/api`, including `GET /api/mastery/weak-concepts`.
