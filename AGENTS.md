This file is the source of truth for agent behavior in this repo.
It prioritizes: safety -> reviewability -> correctness -> speed.

If anything conflicts with your instincts, follow this.

## Disposable work

Use the gitignored `scratch/` folder at the repository root for temporary scripts,
generated artifacts, test logs, screenshots, review evidence, and other disposable work.
Create a task-specific subfolder so concurrent agents do not overwrite each other's files.
Keep reusable scripts and maintained documentation in their normal tracked locations.
Do not create top-level `.audit/` folders or stage files from `scratch/`.
Clean up only files and processes your task created. Preserve verification evidence until
the user no longer needs it.

## Quality Gates

### Database Policy

This repo has two Postgres contexts: the active development app database and the automated test
database. Keep them separate.

- Do not create temporary SQLite databases for migration, e2e, MCP, or app verification.
- Do not run destructive automated tests against the active dev database from `.env`.
- Automated DB/API/MCP/migration tests use Testcontainers Postgres.
- Testcontainers starts one `postgres:16-alpine` container per pytest session.
- Alembic migrations create test schema. Do not use `Base.metadata.create_all()` as the integration
  test schema path.
- Test isolation resets data inside the Testcontainers database and preserves Alembic metadata.
- If active dev schema or data is stale, run migrations against Postgres or intentionally wipe/reseed
  Postgres.
- SQLite compatibility branches may exist in application code for SQLAlchemy connection handling or
  legacy migration support, but SQLite checks are not evidence that the app works in this repo.

### Default Flow

1. Code changes: run the normal Python checks once after edits and before staging:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run ty check
   make test
   ```

2. If formatting is the only failure, run `uv run ruff format .`, then rerun the checks once.
3. Docs-only changes can skip Python checks unless they alter executable examples or setup commands.
4. If tests reveal unrelated failures outside the touched area, do not broaden the change by default. Note the failures and keep the fix scoped.

### Test Commands

- `make test-unit`: pure unit tests; no database and no Docker.
- `make test-db`: API, MCP, integration, and migration tests against Testcontainers Postgres.
- `make test`: full local suite.
- `make test-final`: full suite with fresh-container assumptions for final validation.

### CodeScene Code Health

Run CodeScene on every touched or new source file after edits and before commit. No exceptions for code changes.

- Use CodeScene MCP tools when they are in context:
  - `code_health_score` for pre-edit and post-edit file scores.
  - `code_health_review` for actionable findings when a score is below the gate.
  - `pre_commit_code_health_safeguard` before committing a local change set.
  - `analyze_change_set` for branch-level or PR pre-flight checks.
- If CodeScene MCP is not available, use the installed `cs` CLI for file-level review.
- Use the CodeScene API only for project-wide threshold checks.
- Record pre-edit scores before changing existing source files.
- Post-edit gates:
  - New source files must reach `10.0`, or have zero findings if CodeScene cannot assign a score.
  - Existing files already at `10.0` must remain `10.0`.
  - Existing files below `10.0` must improve.
- If a gate fails, refactor and rerun CodeScene. Do not suppress the finding or commit the regression.
- Never lower `.codescene-thresholds`.

### Escalation Triggers

- Database migrations or SQLAlchemy model changes: include migration upgrade verification against Postgres.
- MCP server changes: include at least one fresh-process MCP tool invocation or integration test.
- Frontend template or CSS changes: inspect the affected route in a browser when feasible.

For a new checkout, run `review-party checkpoint install git` to install the Git hook.
The hook requires working Review Party configuration. Hook setup errors can warn and allow a push.
Agents must also run `review-party checkpoint check pre-push --base <upstream> --head HEAD`
and require a passing result before pushing.

<!-- review-party checkpoints: begin -->
Before pushing, review the change with `review-party run --base <upstream> --head HEAD` and record a verdict for each Finding with `review-party finding record <id>`; the pre-push Checkpoint refuses a push with more than 0 unreviewed lines or a Finding without a verdict. Every fix after a Review needs `review-party run --unreviewed --base <upstream> --head HEAD`. At most 3 Reviews per change; when the Checkpoint says stop, report the unreviewed lines and stop. When a Review does not fit, waive it with `review-party checkpoint waive pre-push --reason "<why>"`.
`review-party checkpoint --help` has details.
<!-- review-party checkpoints: end -->
