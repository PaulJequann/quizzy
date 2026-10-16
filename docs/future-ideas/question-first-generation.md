# Question-First Generation

## Decision

Quizzy should move to a question-first learning model.

Agents should create and maintain the durable question bank. Quizzy should assemble fresh practice
sessions from that bank without requiring an agent. Ordinary quizzes should be disposable practice
containers. Durable learning history should attach to question attempts and roll up through concepts.

Formal exams use reviewer-assigned snapshots of published quizzes. Learners submit once, and the
reviewer releases the score and answer feedback. Averages and retake history remain future work.

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
  - A reviewer-assigned snapshot of a published quiz.
  - Exams keep submissions and scores separate from practice history until the reviewer releases
    the result.

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

- Question health owns quality evidence.
  - App-computed signals from attempts, selection history, concept peers, source provenance, and
    review outcomes.
  - Health influences practice selection and creates repair work when a question appears ambiguous,
    stale, trivial, too difficult, poorly grounded, or poorly discriminating.
  - Agents may repair questions, but the app owns when health is computed and when review items are
    created.

- Exam owns formal assessment.
  - A fixed question set, one learner submission, and reviewer-controlled result release.
  - Averages and retake history remain future work.
  - Exams are intentionally separate from ordinary practice.

## Product Workflow

1. Agent ingests or reads source material.
2. Agent creates grounded questions with concepts, explanations, distractors, and source references.
3. Quizzy stores those questions in the subject question bank.
4. User can start fresh practice from the web app or future PWA:
   - Quick subject practice
   - Concept drill
   - Weak-concept review
   - Mixed mastery review
   - Difficulty-targeted practice
   - Source-based practice
   - Random practice
5. Quizzy assembles practice sessions from existing questions without an agent.
6. User attempts create question-level evidence.
7. Quizzy updates question health and concept mastery from attempt evidence.
8. Quizzy automatically downweights, excludes, or queues unhealthy questions for review.
9. Agents can repair queued question issues through MCP, using source context and attempt evidence.

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
- Prefer healthy questions for mastery-sensitive practice.
- Reserve a small exploration quota for active but untested questions.
- Avoid questions seen too recently unless the requested pool is too small.
- Prefer questions missed recently for review flows.
- Prefer unseen questions for quick/random practice when available.
- Downweight questions that need review.
- Exclude archived or suspect questions from ordinary practice.
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

### Question Health Model

Add a lightweight app-owned question health lifecycle. This can start as computed service logic plus
review items. A dedicated persisted summary table can be added when performance or UI needs justify
it.

Question health signals:

- Attempt count.
- Recent correctness rate.
- Choice distribution.
- Distractors that are never selected.
- Repeated misses after explanation exposure.
- Questions that are almost always correct or almost always missed.
- Questions whose behavior is unlike peer questions linked to the same concepts.
- Last attempted timestamp.
- Last reviewed timestamp.
- Source grounding confidence.
- Learner or maintainer feedback flags.

Health states:

- `untested`
  - Active question without enough attempt evidence.
  - Eligible through an exploration quota.

- `healthy`
  - Normal practice eligibility.

- `needs_review`
  - Eligible only when needed or downweighted.
  - Creates queue work for an agent or maintainer.

- `suspect`
  - Excluded from mastery-sensitive practice until repaired or reviewed.

- `archived`
  - Excluded from ordinary practice.
  - Historical attempts remain valid.

### `question_review_items`

Create this table when question health repair becomes actionable.

Fields:

- `id`
- `question_id FK -> questions.id`
- `subject_id FK -> subjects.id`
- `generation_batch_id nullable FK -> question_generation_batches.id`
- `reason text not null`
- `suggested_action text not null`
  - Examples: `revise_stem`, `improve_distractors`, `clarify_explanation`,
    `reassign_concepts`, `verify_source_grounding`, `archive_question`,
    `generate_sibling_questions`
- `evidence_json text not null default "{}"`
- `status text not null default "open"`
  - Examples: `open`, `resolved`, `dismissed`
- `resolution_notes text not null default ""`
- `created_at`
- `updated_at`

Purpose:

- Turn attempt evidence into concrete repair work.
- Keep poor questions from silently polluting mastery and practice.
- Give agents precise, source-grounded maintenance tasks instead of asking them to search the bank
  opportunistically.

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
  - Practice attempts use `practice`.

Exam submissions remain in the separate `exam_assignments` and `exam_submissions` tables.

Add to `attempt_answers`:

- `answered_at datetime nullable`

Do not create a separate `question_attempts` table in the first implementation. Instead, treat
`attempt_answers` as the question attempt table. Do not rename attempt tables in the first
implementation.

### Current Exam Model

The app assigns a snapshot of a published quiz through `exam_assignments` and stores the learner's
single submission in `exam_submissions`. The reviewer controls when the score and answer feedback
become visible to the learner. A future question-first design can decide whether exams need a link
to durable question-level attempts, averages, or retake history.

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
- Use question health to prefer reliable questions, sample untested questions, downweight questions
  needing review, and exclude suspect or archived questions from ordinary practice.

Primary methods:

- `assemble_practice_session(subject_id, count, strategy, concept_ids=None, difficulty=None)`
- `assemble_mastery_review(subject_id=None, count=None)`
- `assemble_concept_drill(concept_ids, count)`

### Add `QuestionHealthService`

Responsibilities:

- Initialize health state when questions are created.
- Compute per-question learning utility from attempts, selection history, concept peers, and source
  provenance.
- Detect ambiguous, trivial, stale, overused, under-tested, poorly grounded, or poorly
  discriminating questions.
- Feed health state and scores into practice assembly.
- Create review queue items when evidence crosses app-owned thresholds.
- Recommend archive, revision, concept reassignment, source verification, or sibling-question
  generation.

Primary methods:

- `initialize_for_batch(generation_batch_id)`
- `record_attempt_evidence(attempt_id)`
- `score_question_health(question_id)`
- `audit_subject_question_health(subject_id)`
- `list_question_review_queue(subject_id=None)`
- `resolve_question_review_item(item_id, action, notes="")`

Trigger points:

- After question creation:
  - Initialize health records.
  - Audit source, concept, and question coverage.
  - Create review items for obvious structural issues.

- After attempt submission:
  - Update question health.
  - Update concept mastery.
  - Create review items when thresholds are crossed.

- During practice assembly:
  - Exclude archived or suspect questions.
  - Downweight questions needing review.
  - Reserve some slots for active but untested questions.
  - Prefer healthy questions for mastery-sensitive sessions.

- Periodic audit:
  - Find stale, under-tested, overused, low-coverage, or unresolved unhealthy questions.
  - Create or refresh review items.

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

Question health must protect mastery-sensitive flows. A question can retain historical attempts while
being downweighted, marked suspect, or archived for future practice. This avoids deleting evidence
while keeping low-quality questions from continuing to shape learner review.

## MCP Changes

MCP should emphasize question-bank creation and maintenance.

Primary tools:

- `prepare_question_generation_context`
- `create_generated_questions`
- `list_question_bank`
- `list_question_review_queue`
- `prepare_question_repair_context`
- `resolve_question_review_item`
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

MCP does not own question health. The app owns health scoring, queue creation, and practice
eligibility. MCP exposes app-created state and safe repair actions so agents can work on bounded,
source-grounded maintenance tasks.

Agent responsibilities:

- Create source-grounded questions.
- Repair stems, distractors, explanations, and concept assignments when review items request it.
- Generate sibling questions when health evidence shows a coverage gap.
- Resolve review items through app services after making a bounded change.

App responsibilities:

- Observe attempts.
- Compute health.
- Create review items automatically.
- Adjust practice selection automatically.
- Preserve historical attempts even when questions are repaired, marked suspect, or archived.

## UI Changes

Subject detail page:

- Add `Start quick practice`.
- Add `Drill weak concepts` when weak concepts exist.
- Add `Drill concept` actions near concept rows.
- Show a small question review signal when the subject has open question review items.

Mastery page:

- Add `Practice weak concepts`.
- Add `Practice improving concepts`.

Question bank UI:

- Show generated questions independent of quizzes.
- Filter by subject, concept, difficulty, source, status, and last attempted.
- Show health state and open review status.
- Support maintainer actions to archive, dismiss, or queue agent repair.

Practice result page:

- Emphasize question and concept performance.
- Treat the session score as secondary.

Maintenance UI:

- Review open question issues by subject, source, concept, and generation batch.
- Show the flagged question, source context, attempt evidence, choice distribution, and suggested
  action.
- Allow human review directly and agent-assisted repair through MCP.

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
3. Add initial `QuestionHealthService` computed scoring.
4. Move generated question validation into shared question-bank logic.
5. Keep old generated quiz services as wrappers or compatibility paths.

Phase 3: UI and MCP

1. Add app-native quick practice actions.
2. Add MCP question-bank tools.
3. Add MCP question review and repair tools.
4. Update MCP guidance so agents create questions first and repair app-created review items.
5. Keep agent-created quiz tools for explicit use.

Phase 4: Question health lifecycle

1. Add `question_review_items`.
2. Create review items after attempts and periodic audits.
3. Feed health state into practice assembly.
4. Add question health indicators and review queue UI.
5. Add agent-assisted repair workflow.

Phase 5: Domain language cleanup

1. Use "practice" for app-assembled disposable sessions in new UI.
2. Keep "quiz" only for existing compatibility surfaces and manually saved quizzes.
3. Defer table renames until the model has stabilized.

## Non-Goals

- Do not remove existing generated quiz MCP tools immediately.
- Do not force all practice sessions to be saved permanently.
- Do not duplicate questions per practice session.
- Do not move question ownership to concepts.
- Do not make quiz containers the source of mastery truth.
- Do not require users to manually trigger question health analysis.
- Do not make agents responsible for discovering unhealthy questions opportunistically.

## Implementation-Ready Acceptance Criteria

- Agents can create questions without creating a quiz.
- The app can assemble a practice session from existing questions without an agent.
- A question can appear in multiple practice sessions.
- Question attempts aggregate across all practice sessions.
- Concept mastery is computed from question attempts, not from quiz identity.
- Disposable practice sessions store strategy and selection metadata.
- App-owned health scoring runs after question creation and attempt submission.
- Practice assembly uses question health without requiring an agent.
- Question review items are created automatically from health evidence.
- Agents can repair queued question issues through MCP with source context and attempt evidence.
- Existing quiz-taking flow continues to work during migration.
- Existing attempts remain readable after migration.

## Final Principle

Agents create durable learning material. The app assembles fresh practice. Question attempts carry
learning history. Question health turns that history into automatic quality control and bounded
repair work. Reviewer-assigned exams handle formal assessment and release results after review.
