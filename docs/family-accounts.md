# Set up study accounts

Use Postgres 15 or later. Compose and automated tests use Postgres 16.
Set `DATABASE_URL` to your Postgres database and `APP_BASE_URL` to the exact browser URL.
Use HTTPS for a hosted installation so the session cookie is secure. The app rejects cross-origin and
originless mutation requests, including login, logout, and HTMX actions.

Run the migration and provision the initial family:

```bash
uv run alembic upgrade head
uv run python -m app.auth.provision \
  --parent-email parent@example.com \
  --learner-email learner@example.com \
  --learner-name Learner
```

The command prompts for both passwords without echoing them. Choose at least 8 characters
for each password. Replace the example emails with your own sign-in addresses. Provisioning
activates the migrated parent and creates the learner in one transaction. It prints their
workspace IDs. It refuses to overwrite an already provisioned parent.

Existing material, attempts, settings, and feedback belong to the migrated parent. Before
provisioning, that account is inactive and has no usable password. There is no public signup.
The ownership migration is intentionally irreversible; restore a database backup to roll it back.

For the disposable smoke Compose stack, run provisioning in its web container:

```bash
docker compose -f docker-compose.smoke.yml up --build -d
docker compose -f docker-compose.smoke.yml exec web uv run python -m app.auth.provision \
  --parent-email parent@example.com \
  --learner-email learner@example.com \
  --learner-name Learner
```

Open `http://127.0.0.1:18000`. The smoke configuration targets the fresh database's parent
workspace `1` for MCP. Its tools remain unavailable until that parent is provisioned.

Set `MCP_ADMIN_USER_ID` in `.env` to the printed parent ID. To add the sample study material,
run this command with the desired destination ID:

```bash
uv run python -m scripts.seed --workspace-id 1
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For access from another device on your home network, set `APP_BASE_URL` to the exact LAN URL
both users will open, for example `http://192.168.1.20:8000`, then run:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open that configured URL on both devices. Do not substitute `localhost` or a different hostname:
the mutation origin must match `APP_BASE_URL`. Restart the app after changing configuration.

Open `/login`. The reviewer can use **Switch workspace** to choose their own study space or
a learner's library. In the learner's workspace, the reviewer can manage material, inspect
**Practice history**, open results, and view mastery. A reviewer preview cannot submit attempts
or feedback for the learner.

Learners see their published quizzes, results, and progress. They cannot access the editor,
management APIs, or another workspace. The results page can submit feedback for their own answers. Draft quizzes remain hidden until the reviewer publishes
them on the quiz detail page. Past results remain available if a quiz is later archived.

## Prepare the next practice

In the learner's workspace, open **Prepare next practice**. Copy the teaching brief to your
agent. It includes bounded mastery evidence, due state, recent wrong answers, and source
references. Thin evidence is marked explicitly. The app does not run an AI model.

The agent can request the same brief through `prepare_next_practice`. Every MCP tool requires
an explicit `workspace_id`, including reads. The configured administrator must remain active.
The stdio process reads `DATABASE_URL` and `MCP_ADMIN_USER_ID` from its environment or `.env`:

```bash
uv run python mcp_server.py
```

Hosted MCP additionally requires `APP_BASE_URL` and `MCP_BEARER_TOKEN`. Authentication cannot
be disabled for HTTP. Keep that token private; it grants the configured parent's authority.
Every generated or assembled quiz from MCP stays a draft. Review it in the browser and use
**Publish** to make it available to the learner.

Automated verification uses the session's disposable Testcontainers Postgres database.
Do not run destructive automated checks against the development database in `.env`.

## Database constraints

The family migration owns the composite workspace foreign keys and their supporting unique
constraints. The ORM retains the original single-column foreign keys for existing relationship
joins. Both sets are intentional: Postgres enforces workspace consistency, including nullable
references, while study services retain their existing relationship mapping.

`app/migration_policy.py` excludes these migration-owned constraints from automatic removal by
Alembic autogenerate. Change them explicitly in a migration. Integration tests compare every
private model and reference with the live migrated Postgres constraints, so adding an unprotected
model or relationship fails the suite.

If old answers contain a selected choice from a different question, the upgrade stops before
changing rows and reports the answer IDs. Review those records and repair the choice references
before retrying the upgrade.
