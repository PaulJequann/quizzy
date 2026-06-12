# Question-First Generation

## Decision

Quizzy should move to a question-first learning model.

Agents should create and maintain the durable question bank. Quizzy should assemble fresh practice
sessions from that bank without requiring an agent. Ordinary quizzes should be disposable practice
containers. Durable learning history should attach to question attempts and roll up through concepts.

Formal exams should be a separate future model for fixed assessments, durable scores, averages, and
retake history.

## Terminology

- Question bank
  - The durable collection of active questions for a subject.
  - Questions own answer choices, explanations, difficulty, source provenance, and concept links.

- Practice session
  - A disposable generated delivery experience assembled from existing questions.
  - Use "practice" in new UI copy. Existing "quiz" UI copy can remain until that surface is touched.
  - It records selection context and ordering, not long-term learning ownership.

- Question attempt
  - The durable record that a user answered a specific question.
  - This is the core unit of learning history.

- Concept mastery
  - Aggregated from question attempts through question-concept links.

- Exam
  - A future fixed assessment object.
  - Exams are not disposable. Exams own formal scoring and attempt history.

## Current State

Current schema is already partially compatible with question-first generation:

- `questions` belongs to one `subject`.
- `questions` owns `choices`.
- `questions` links to many `concepts` through `question_concepts`.
- `quizzes` links to many `questions` through `quiz_questions`.
- `concepts` links to many `questions` through `question_concepts`.
- `attempt_answers` already links directly to `questions` and stores answer snapshots.

The main issue is not relational capability. The issue is product and service orientation:

- Existing generated workflows create a quiz and questions together.
- Current data behaves as if each question belongs to one quiz, even though the schema allows reuse.
- `attempts.quiz_id` makes quiz containers look more durable than they should be for ordinary
  practice.

## Target Ownership Boundaries

- Subject owns the namespace.
  - Subjects group questions, concepts, source documents, and practice/exam experiences.

- Question owns learning content.
  - Stem, choices, correct answer, explanation, difficulty, source provenance, and status.
  - Questions are reusable across many practice sessions.

- Concept owns taxonomy.
  - Concepts classify questions.
  - Concepts do not own questions.
  - Concepts can be merged, aliased, proposed, or archived without deleting question history.

- Practice session owns delivery context.
  - Strategy, selected question order, count, difficulty filters, concept filters, and timestamps.
  - Practice sessions are disposable and can be pruned later if question attempts remain.

- Question attempt owns durable learning history.
  - Question id, selected choice, correctness, snapshots, and timestamps.
  - Optional pointer to practice session or exam attempt for context.

- Exam owns formal assessment.
  - Fixed question set, durable scores, averages, retakes, and history.
  - Exams are intentionally separate from ordinary practice.

## Product Workflow

1. Agent ingests or reads source material.
2. Agent creates grounded questions with concepts, explanations, distractors, and source references.
3. Quizzy stores those questions in the subject question bank.
4. Quizzy can assemble practice sessions from existing questions without an agent.
5. User can start fresh practice from the web app or future PWA:
   - Quick subject practice
   - Concept drill
   - Weak-concept review
   - Mixed mastery review
   - Difficulty-targeted practice
   - Source-based practice
   - Random practice

Agent-created quizzes remain supported for explicit agent-directed workflows, but they are not the
default product path.

## App-Owned Practice Assembly

Practice assembly must be deterministic app logic.

Required strategies:

- `random_subject`
  - Select N active questions from a subject.

- `concept_drill`
  - Select N active questions tagged to one or more concepts.

- `mastery_review`
  - Prioritize weak and improving concepts.
  - Include some strong concepts for retention.

- `spaced_review`
  - Prioritize questions or concepts missed recently or not seen recently.

- `difficulty_balanced`
  - Select a target distribution across beginner, intermediate, and advanced questions.

- `source_based`
  - Select questions created from one source document or source collection.

Selection rules:

- Prefer active questions.
- Avoid questions seen too recently unless the requested pool is too small.
- Prefer questions missed recently for review flows.
- Prefer unseen questions for quick/random practice when available.
- Preserve selected order in a join table.
- Store why each question was selected.

## Target Schema

### `questions`

Add:

- `source_document_id nullable FK -> source_documents.id`
- `source_excerpt text nullable`
- `generation_batch_id nullable FK -> question_generation_batches.id`

Keep:

- `subject_id`
- `stem`
- `explanation`
- `difficulty`
- `status`
- `source_reference`

`source_reference` remains useful for human-readable citations and compatibility. Structured source
linkage should use `source_document_id`.

### `question_generation_batches`

Create this table.

Fields:

- `id`
- `subject_id FK -> subjects.id`
- `source_document_id nullable FK -> source_documents.id`
- `generator_type text not null`
  - Examples: `mcp_agent`, `manual`, `import`
- `generator_name text nullable`
- `settings_json text not null default "{}"`
- `notes text not null default ""`
- `created_at`
- `updated_at`

Purpose:

- Track which questions were created together.
- Support concept stewardship audits.
- Support source-based question browsing and cleanup.

### Practice Session Model

Use the existing `quizzes` table as the short-term practice session container. Do not introduce a
new table in the first implementation.

Add to `quizzes`:

- `assembly_strategy text nullable`
- `assembly_params_json text not null default "{}"`
- `is_disposable boolean not null default false`

Update `QuizType`:

- Keep `standard`
- Keep `review`
- Keep `generated`
- Add `practice`

Meaning:

- `practice`: app-assembled disposable practice session.
- `generated`: agent-created quiz, retained for explicit agent workflows.
- `review`: deterministic weak-concept review retained for compatibility in the first migration.
- `standard`: manually authored/saved quiz.

### `quiz_questions`

Add:

- `selection_reason text not null default ""`
- `selection_score float nullable`

Purpose:

- Explain why a question was selected.
- Support debugging and future UI transparency.

### Question Attempt Model

Keep existing `attempts` and `attempt_answers` short term.

Interpretation:

- `attempts` becomes the practice-session attempt container.
- `attempt_answers` is the durable question-level learning record.

Add to `attempts`:

- `session_type text not null default "practice"`
  - Future values: `practice`, `exam`

Add to `attempt_answers`:

- `answered_at datetime nullable`

Do not create a separate `question_attempts` table in the first implementation. Instead, treat
`attempt_answers` as the question attempt table. Do not rename attempt tables in the first
implementation.

### Future Exam Model

Do not implement exams in the question-first MVP.

When ready, add:

- `exams`
- `exam_questions`
- `exam_attempts`

Exam attempts can reuse `attempt_answers` or a renamed `question_attempts` table by linking through
`exam_attempt_id`.

## Service Changes

### Add `QuestionBankService`

Responsibilities:

- Create generated questions without requiring a quiz.
- Validate choices, correct answer count, source grounding, and concept count.
- Attach questions to subject, concepts, source document, and generation batch.
- Return created question ids and generation batch metadata.

Primary methods:

- `create_generated_questions(subject_name, questions, source_document_id=None, batch_metadata=None)`
- `list_question_bank(subject_id, filters)`

### Add `PracticeAssemblyService`

Responsibilities:

- Select existing questions.
- Create a disposable practice quiz/session.
- Link selected questions through `quiz_questions`.
- Store strategy, params, selection reason, and selection score.

Primary methods:

- `assemble_practice_session(subject_id, count, strategy, concept_ids=None, difficulty=None)`
- `assemble_mastery_review(subject_id=None, count=None)`
- `assemble_concept_drill(concept_ids, count)`

### Update `GenerationService`

Current generated quiz methods stay for compatibility, but become secondary:

- `create_generated_quiz`
- `add_questions_to_quiz`
- `create_generated_quiz_with_questions`

New default generated path:

- create questions first
- optionally assemble a practice session from those questions

### Update Mastery Calculation

Mastery should continue to aggregate through:

`attempt_answers -> questions -> question_concepts -> concepts`

This already matches the desired model. Future changes should make this relationship more explicit
in naming and service boundaries.

## MCP Changes

MCP should emphasize question-bank creation and maintenance.

Primary tools:

- `prepare_question_generation_context`
- `create_generated_questions`
- `list_question_bank`
- `suggest_concept_assignments`
- `resolve_concept_review_queue`

Secondary compatibility tools:

- `create_generated_quiz`
- `create_generated_quiz_with_questions`

App-owned practice tools:

- `assemble_practice_session`
- `assemble_mastery_review`
- `assemble_concept_drill`

Agents can call assembly tools. The app UI must also call the same services without an agent.

## UI Changes

Subject detail page:

- Add `Start quick practice`.
- Add `Drill weak concepts` when weak concepts exist.
- Add `Drill concept` actions near concept rows.

Mastery page:

- Add `Practice weak concepts`.
- Add `Practice improving concepts`.

Question bank UI:

- Show generated questions independent of quizzes.
- Filter by subject, concept, difficulty, source, status, and last attempted.

Practice result page:

- Emphasize question and concept performance.
- Treat the session score as secondary.

## Migration Plan

Phase 1: Schema and compatibility

1. Add structured question provenance fields.
2. Add `question_generation_batches`.
3. Add practice assembly metadata to `quizzes`.
4. Add selection metadata to `quiz_questions`.
5. Add `session_type` to `attempts`.
6. Add `answered_at` to `attempt_answers`.
7. Backfill existing `attempts.session_type = "practice"`.
8. Backfill existing generated questions into one synthetic generation batch per subject.

Phase 2: Question-first services

1. Add `QuestionBankService`.
2. Add `PracticeAssemblyService`.
3. Move generated question validation into shared question-bank logic.
4. Keep old generated quiz services as wrappers or compatibility paths.

Phase 3: UI and MCP

1. Add app-native quick practice actions.
2. Add MCP question-bank tools.
3. Update MCP guidance so agents create questions first.
4. Keep agent-created quiz tools for explicit use.

Phase 4: Domain language cleanup

1. Use "practice" for app-assembled disposable sessions in new UI.
2. Keep "quiz" only for existing compatibility surfaces and manually saved quizzes.
3. Defer table renames until the model has stabilized.

## Non-Goals

- Do not implement formal exams in the MVP.
- Do not remove existing generated quiz MCP tools immediately.
- Do not force all practice sessions to be saved permanently.
- Do not duplicate questions per practice session.
- Do not move question ownership to concepts.
- Do not make quiz containers the source of mastery truth.

## Implementation-Ready Acceptance Criteria

- Agents can create questions without creating a quiz.
- The app can assemble a practice session from existing questions without an agent.
- A question can appear in multiple practice sessions.
- Question attempts aggregate across all practice sessions.
- Concept mastery is computed from question attempts, not from quiz identity.
- Disposable practice sessions store strategy and selection metadata.
- Existing quiz-taking flow continues to work during migration.
- Existing attempts remain readable after migration.

## Final Principle

Agents create durable learning material. The app assembles fresh practice. Question attempts carry
learning history. Exams, when added, handle formal assessment.
