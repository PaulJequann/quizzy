# Quizzy

Quizzy is a small FastAPI quiz web app POC. It tracks reusable questions, subject-scoped concepts, quiz attempts, and concept-level mastery. It uses Jinja templates, HTMX, Tailwind CSS v4 in the browser, SQLAlchemy, Alembic, Postgres, uv, Ruff, ty, and pytest.

## Local setup

```bash
uv sync
cp .env.example .env
```

Start Postgres locally or use Docker Compose for the database. The default `DATABASE_URL` is:

```text
postgresql+psycopg://quizzy:quizzy@localhost:5432/quizzy
```

## Run locally

```bash
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
uv run pytest
uv run ruff check .
uv run ruff format .
uv run ty check
```

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
