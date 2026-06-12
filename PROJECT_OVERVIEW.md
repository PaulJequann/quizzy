Scaffold V1 of a small quiz web app POC.

Context:
I use Obsidian for notes and eventually want an AI-assisted workflow where agents generate quizzes from notes. For V1, do not implement Obsidian integration, MCP, AI generation, or PWA features yet. This first version should prove the core app experience: creating quizzes, taking quizzes, submitting answers, and viewing results/grades.

Tech stack:

* Python
* uv for dependency/project management
* FastAPI
* Postgres
* SQLAlchemy or SQLModel
* Alembic for migrations
* Jinja templates
* HTMX for lightweight interactivity
* Tailwind CSS v4 for styling
* Docker
* Ruff for linting/formatting
* ty for type checking if practical
* pytest for basic tests

Goal:
Create a fully working POC with a basic but polished UI and dummy seeded quiz data.

Core features for V1:

1. Quiz list page

   * Shows available quizzes
   * Shows title, description, question count, difficulty, and status
   * Link/button to take each quiz

2. Quiz detail page

   * Shows quiz metadata
   * Shows questions in read-only preview mode
   * Button to start/take the quiz

3. Create quiz page

   * Simple server-rendered form to create a quiz manually
   * Fields: title, description, topic/tags, difficulty
   * After creation, allow adding multiple-choice questions

4. Add/edit questions

   * Each question should have:

     * stem/question text
     * four answer choices
     * one correct answer
     * optional explanation
     * optional source reference field for future Obsidian/note linkage
   * Keep this simple; no rich editor needed

5. Take quiz flow

   * User can answer multiple-choice questions
   * Use server-rendered Jinja pages enhanced with HTMX where it helps
   * Submit quiz attempt
   * Store selected answers in the database

6. Results page

   * Show score percentage
   * Show number correct / total
   * Show pass/fail or letter grade
   * Show each question, selected answer, correct answer, and explanation
   * Highlight correct/incorrect answers clearly

7. Dummy/seed data

   * Add a seed command or script that creates at least:

     * 3 quizzes
     * 5 questions per quiz
     * realistic topics like AWS, Linux, and Python
   * The app should be usable immediately after running migrations and seed data.

Data model:
Create a clean relational schema. Suggested entities:

* Quiz
* Question
* Choice
* Attempt
* AttemptAnswer

Suggested fields:
Quiz:

* id
* title
* description
* topic
* difficulty
* status: draft/published
* created_at
* updated_at

Question:

* id
* quiz_id
* stem
* explanation
* source_reference
* order_index
* created_at
* updated_at

Choice:

* id
* question_id
* text
* is_correct
* order_index

Attempt:

* id
* quiz_id
* score
* total_questions
* correct_count
* started_at
* submitted_at

AttemptAnswer:

* id
* attempt_id
* question_id
* selected_choice_id
* is_correct

Routes/pages:

* GET / — redirect to /quizzes
* GET /quizzes — quiz list
* GET /quizzes/new — create quiz form
* POST /quizzes — create quiz
* GET /quizzes/{quiz_id} — quiz detail
* GET /quizzes/{quiz_id}/questions/new — add question form
* POST /quizzes/{quiz_id}/questions — create question
* GET /quizzes/{quiz_id}/take — take quiz
* POST /quizzes/{quiz_id}/submit — submit quiz attempt
* GET /attempts/{attempt_id}/results — results page

API routes:
Also add minimal JSON API routes under /api for future agent integration, but keep them simple:

* GET /api/quizzes
* GET /api/quizzes/{quiz_id}
* POST /api/quizzes
* POST /api/quizzes/{quiz_id}/questions
* POST /api/quizzes/{quiz_id}/attempts

Frontend:
Use Jinja templates with a shared base layout.
Use HTMX for small interactions where helpful, such as:

* adding a question without full page reload
* publishing a quiz
* loading question forms
* submitting lightweight actions

Do not create a React/Svelte/Vue SPA.

Use Tailwind CSS v4 for styling. Keep the UI clean, responsive, and mobile-friendly. It should feel like a lightweight study app.

Project structure:
Use a maintainable structure similar to:

app/
main.py
config.py
database.py
models.py
schemas.py
services/
quiz_service.py
grading_service.py
routes/
web.py
api.py
templates/
base.html
quizzes/
attempts/
static/
css/
alembic/
tests/
scripts/
seed.py
Dockerfile
docker-compose.yml
pyproject.toml
README.md

Docker/local dev:
Create a Dockerfile and docker-compose.yml that runs:

* FastAPI app
* Postgres database

The app should run locally with a simple command documented in README.

Include environment variables:

* DATABASE_URL
* APP_ENV
* SECRET_KEY placeholder, even if auth is not implemented yet

Quality tooling:
Configure:

* uv
* ruff
* ty if practical
* pytest
* alembic

Add useful commands in README:

* install deps
* run app locally
* run with docker compose
* run migrations
* seed database
* run tests
* run lint/type checks

Testing:
Add basic pytest tests for:

* app starts
* quiz list route returns 200
* seed data creates quizzes/questions
* grading logic calculates correct score

Acceptance criteria:
The POC is complete when:

1. I can start the app with Docker Compose.
2. Postgres is running and migrations apply successfully.
3. I can seed dummy quiz data.
4. I can open the web UI and see quizzes.
5. I can create a quiz manually.
6. I can add multiple-choice questions.
7. I can take a quiz.
8. I can submit answers.
9. I can view a results page with score, grade, correct answers, selected answers, and explanations.
10. Tests, linting, and formatting commands are documented and working.

Important constraints:

* Do not implement auth yet.
* Do not implement MCP yet.
* Do not implement PWA/offline mode yet.
* Do not implement AI quiz generation yet.
* Do not overbuild the frontend.
* Prioritize a working vertical slice over architectural complexity.
* Keep the app simple, clean, and easy to extend later.

