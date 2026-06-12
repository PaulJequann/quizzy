# Concept Stewardship Workflow

## Problem

Generated quizzes can create concept entropy: agents may turn individual facts, exam gotchas,
protocol variants, or question titles into separate concepts. That makes mastery noisy and forces
users to understand internal cleanup operations instead of focusing on learning.

## Idea

Treat concepts as a maintained knowledge map, not passive tags. Concept stewardship should run as an
app-owned lifecycle step whenever new concepts are introduced. MCP tools can expose and assist the
workflow, but the app should trigger stewardship automatically.

## Automatic Trigger Points

1. During generated quiz creation
   - Before persisting generated questions, evaluate proposed concepts against existing concepts,
     aliases, source document context, and subject policy.
   - Automatically reuse obvious existing concepts.
   - Mark uncertain new concepts as proposed instead of canonical.
   - Warn or queue review when a generated batch exceeds the expected concept budget.

2. After an ingestion or generation batch
   - Audit how many concepts were created from one source.
   - Detect many single-use concepts from the same batch.
   - Detect semantically close concepts that look like duplicates or overly granular variants.
   - Create review queue items for high-risk batches.

3. Before mastery display
   - Hide or de-emphasize proposed concepts by default so noisy tags do not immediately pollute the
     learner's mastery map.
   - Surface a small maintenance signal such as "Concept review needed" when the app has queued
     stewardship work.

4. Periodic background audit
   - Run a lightweight audit on startup, daily, or when concept state is stale.
   - Flag single-use concepts after a threshold, likely duplicate names, aliases, and concepts from
     suspicious generation batches.

## Proposed App Services

- `ConceptAssignmentService`
  - Runs when generated questions are added.
  - Normalizes concept names.
  - Resolves aliases and obvious matches.
  - Enforces concept budget rules.
  - Returns canonical concept ids plus warnings or proposals.

- `ConceptAuditService`
  - Runs after generated quiz creation and periodically.
  - Detects duplicate, granular, or low-use concepts.
  - Creates review queue items when concept quality needs attention.

- `ConceptReviewQueue`
  - Stores pending concept cleanup decisions.
  - Supports approve, merge, alias, rename, reject, and archive decisions.
  - Gives both the UI and MCP a shared app-state queue to work from.

## Possible Data Model

- `concepts.status`: `active`, `proposed`, `archived`
- `concept_aliases`: alternate names mapped to canonical concepts
- `concept_merge_events`: audit trail of source concepts merged into target concepts
- `concept_review_items`: queued stewardship decisions with reason, confidence, and source batch

## MCP Role

MCP should not be the only way stewardship happens. MCP tools should operate over app-created state:

- `prepare_concept_assignment(subject_name, proposed_concepts)`
- `review_concept_queue(subject_name)`
- `approve_concept`
- `alias_concept`
- `merge_concepts`
- `reject_concept`

The agent can help resolve queued decisions, but the app should create the queue automatically.

## Minimum Viable Version

Run stewardship automatically inside generated quiz creation:

1. Resolve proposed concept names against existing concepts.
2. If a generated batch creates more than six concepts for one subject or source document, create a
   concept review item.
3. Store uncertain concepts as proposed.
4. Hide proposed concepts from mastery by default.
5. Show a small concept review signal in the UI.

This closes the loop: generation creates concepts, mastery exposes concept quality problems, and
stewardship feeds corrections back into future generation.
