---
name: verify
description: Drive Quizzy's browser study, family accounts, authoring, and teaching workflow against disposable Postgres. Use after web changes or when proving user-visible behavior. Includes MCP verification pointers.
---

# Verify Quizzy

Read [the feature index](features/README.md), then the matching recipe. This skill lives in
Codex's project skill directory. `.claude/skills/verify` links here for Claude Code discovery.

## Launch

Run from the repository root. Docker must be available. The launcher uses one fresh
Testcontainers `postgres:16-alpine`, applies Alembic, provisions synthetic accounts, and
seeds private published quizzes and drafts. It overrides `.env` database settings before
importing the app. It never migrates or resets the active development database.

```bash
uv sync --locked
RUN_DIR="$PWD/scratch/verify-$(date -u +%Y%m%dT%H%M%SZ)-$$"
PYTHONPATH="$PWD" uv run --no-sync python .agents/skills/verify/scripts/launch.py \
  --port 8022 --evidence "$RUN_DIR"
```

Keep that command in a managed execution session. Wait for `READY`, `instance.json`, and
`ready` before driving. The evidence directory must not already exist. Port 8022 must be
unused; choose another port with `--port` for concurrent runs. Use a separate browser tab
per run. Each run has a separate database, accounts, logs, and evidence directory.

Synthetic sign-ins are `parent@example.com` / `Verify-Parent-2026` and
`learner@example.com` / `Verify-Learner-2026`. These are disposable fixture credentials.
`instance.json` records the URL, launcher PID, container ID, workspace and quiz IDs.
`revision` is committed HEAD, not proof of uncommitted source identity. Save `git diff`
and `git status --short` with the evidence when verifying dirty code.

## Doctor

Run this read-only check before driving, and again whenever the instance looks wrong.
Use the same `RUN_DIR` from launch in the inspecting shell.

```bash
uv run --no-sync python - "$RUN_DIR" <<'PY'
import json, subprocess, sys
from pathlib import Path
from urllib.request import urlopen
run = Path(sys.argv[1])
instance = json.loads((run / 'instance.json').read_text())
assert not (run / 'stopped').exists(), 'Run has stopped'
command = subprocess.check_output(['ps', '-p', str(instance['launcher_pid']), '-o', 'args='], text=True)
assert 'verify/scripts/launch.py' in command and str(run) in command, command
assert subprocess.check_output(['docker', 'inspect', '-f', '{{.State.Running}}', instance['container_id']], text=True).strip() == 'true'
server_pid = (run / 'ready').read_text().strip()
server = subprocess.check_output(['ps', '-p', server_pid, '-o', 'args='], text=True)
assert 'uvicorn' in server and 'app.main:app' in server, server
with urlopen(instance['url'] + '/login', timeout=3) as response:
    assert response.status == 200
    assert b'Sign in to Quizzy' in response.read()
print('DOCTOR_OK', instance['url'], instance['revision'])
PY
```

The login page proves readiness, not authentication. The mapped login flow proves the
fixture credentials work. On failure, inspect `server.log` and stop this run. Do not attach
to a user's existing server or reuse their login session.

## Drive

Use T3 collaborative browser tools when available. Call `preview_status`, then
`preview_open` with `reuseExistingTab=false` and the manifest URL plus `/login`. Keep the
returned `tabId` on every call. Inspect with `preview_snapshot` before interactions.
Use `preview_type` and `preview_click` with the exact locators in the feature recipes.
After login or submit, wait for the expected route in a fresh snapshot before continuing.
Use `preview_evaluate` only for reading rendered state or testing browser capability
fallbacks. Do not use it to submit forms, set authentication, or manufacture progress.

If T3 tools are absent, use the available browser driver with the same role/CSS locators.
If the browser cannot reach loopback, report that limitation; do not expose the app to a
network or claim the UI verified. API tests cannot substitute for the browser path.

MCP is a secondary interface. For an MCP change, run `make test-db` and require the fresh
process test in `tests/test_family_mcp.py` to pass. Every data tool needs `workspace_id`.
Do not point a verification MCP process at `.env` or the active database. The browser
launcher does not launch hosted MCP or prove its transport.

## Evidence

Save each action, expected result, actual route/text, and pass/fail in `$RUN_DIR/proof.md`.
Save snapshots before and after the feature. `preview_snapshot` with `save=true` returns
a screenshot path; copy it into `RUN_DIR`. Include the feature ID and entry point in names.
Capture browser diagnostics, `server.log`, `instance.json`, doctor output, source status,
and the teardown result. Record skipped entries separately.

Exercise real clicks and forms. Prove persistence through a second page or sign-in after
the mutation. A final screenshot alone does not prove the action. Fixtures create initial
data only; they cannot count as proof of authoring or grading. No external system is mocked.
The browser normally fetches CDN scripts and fonts, so this is not a network-free run.

## Cleanup

Send SIGTERM only to the launcher PID from this run's `instance.json`, after checking
`ps -p PID -o args=` still contains this launch command and evidence path. The launcher
stops its exact child PID and removes its own Postgres container. Wait for its execution
session to finish and require `cleaned`. Check `docker inspect CONTAINER_ID` reports no
such container and `ps -p SERVER_PID` reports no process. Do this after failed drives too.

If the launcher was forcibly killed, inspect the recorded child PID and container ID,
then stop only those owned resources. Never kill by process name or prune Docker globally.
Keep the evidence directory, screenshots, logs, and source diff. Confirm they still exist
after cleanup. Close only the browser tab created for this run.

## Helpers

`scripts/launch.py` is executable. Its invocation is in Launch. It must run through
`uv run` with `PYTHONPATH` set to the repo root, so app and fixture imports resolve.
It fails if the requested port is occupied or its evidence folder already exists.
