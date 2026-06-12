This file is the source of truth for agent behavior in this repo.
It prioritizes: safety -> reviewability -> correctness -> speed.

If anything conflicts with your instincts, follow this.

## Quality Gates

### Default Flow

1. Code changes: run the normal Python checks once after edits and before staging:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run ty check
   uv run pytest
   ```

2. If formatting is the only failure, run `uv run ruff format .`, then rerun the checks once.
3. Docs-only changes can skip Python checks unless they alter executable examples or setup commands.
4. If tests reveal unrelated failures outside the touched area, do not broaden the change by default. Note the failures and keep the fix scoped.

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
