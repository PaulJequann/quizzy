# Source-to-Mastery Impact Graph

## Problem

Quizzy's hosted MCP ingestion plan creates the foundation for durable source provenance:

- source documents have stable identity;
- source snapshots preserve exact source versions;
- generated questions can reference the source snapshot that grounded them;
- re-ingesting changed content can identify questions grounded in older source versions.

That is enough for safe hosted ingestion, but it does not yet answer the deeper maintenance question:

```text
When a source changes, what learning assets became stale, which concepts are affected,
which questions should be repaired or regenerated, and which mastery evidence needs review?
```

Without an impact model, source snapshots are mostly provenance. The next product layer is turning
source changes into actionable learning maintenance.

## Product Intent

Treat content ingestion as the beginning of a living knowledge-maintenance loop.

Quizzy should know:

- why a question exists;
- which source version grounded it;
- which source span supported the answer, explanation, or distractors;
- which concept the source span teaches;
- whether that source span changed;
- whether a changed span affects existing questions, concept coverage, or learner mastery.

The goal is not to reset mastery whenever a note changes. The goal is to produce targeted repair,
generation, and review recommendations based on source impact.

## Desired Experience

When a source document changes, Quizzy should be able to show a focused impact summary:

```text
AWS/S3/storage-classes.md changed.

Impact:
- 4 concepts affected
- 12 questions checked
- 7 questions still valid
- 3 questions need edits
- 2 questions should be retired
- 1 concept needs new application questions
- 2 mastered concepts are review-due because their source changed
```

Useful actions:

- repair affected questions;
- retire obsolete questions;
- generate questions for newly uncovered material;
- schedule targeted review for affected concepts;
- inspect the old and new source excerpts that drove each recommendation.

## Scope And Timing

This is a future feature, not part of the first hosted MCP content-ingestion implementation.

Prerequisites:

- content-based source ingestion is the only MCP ingestion path;
- source snapshots exist in v1;
- generated questions store `source_snapshot_id`;
- question repair context can load original and current source content;
- content re-ingestion can identify changed source documents.

Only after those foundations are working should Quizzy add source-span tracking and source-to-mastery
impact computation.

## Core Model

The impact graph connects source changes to learning consequences:

```text
SourceDocument
  -> SourceSnapshot
    -> SourceSpan
      -> Concept
        -> Question
          -> UserConceptMastery
            -> ReviewRecommendation
```

The critical addition is `SourceSpan`: a stable chunk of source material that can ground concepts and
questions.

## Source Spans

Segment each source snapshot into deterministic spans.

For Markdown v1, spans can start simple:

- one span per heading section;
- optional paragraph-level spans inside long sections;
- code blocks or definition blocks as their own spans when useful.

Possible schema:

```text
source_spans
  id
  source_document_id
  source_snapshot_id
  stable_span_key
  heading_path
  start_char
  end_char
  content
  content_sha256
  semantic_fingerprint
  created_at
```

For v1, `stable_span_key` can be based on:

```text
source_key + heading_path + normalized_heading_slug
```

If headings move or change, later versions can use content similarity or explicit aliases to match
old and new spans.

## Grounding Links

Questions and concepts should link to source spans once span tracking exists.

Concept grounding:

```text
concept_source_spans
  concept_id
  source_span_id
  role          -- defines, explains, example, exception, prerequisite
  confidence
  created_by   -- human, agent, system
  created_at
```

Question grounding:

```text
question_source_spans
  question_id
  source_span_id
  source_snapshot_id
  source_excerpt
  grounding_role  -- answer_source, distractor_source, explanation_source
  confidence
  created_at
```

Generated question tools should eventually ask agents to include source span ids and grounding
reasons, not only source document ids.

## Change Detection

On source re-ingest:

1. Segment the new snapshot into spans.
2. Match old spans to new spans.
3. Classify each span change.
4. Find affected questions and concepts.
5. Create repair, generation, and review recommendations.

V1 span statuses:

```text
unchanged
changed
removed
new
```

Later semantic statuses:

```text
moved
minor_wording_change
meaning_changed
new_material
removed_material
potential_contradiction
```

V1 can use hash and text-similarity comparisons. LLM-based semantic classification should be a later
enhancement, not the first dependency.

## Impact Records

Possible impact tables:

```text
source_change_impacts
  id
  source_document_id
  old_snapshot_id
  new_snapshot_id
  impact_type     -- added, removed, changed, contradicted, moved
  severity        -- low, medium, high
  summary
  created_at
```

```text
question_staleness_assessments
  id
  question_id
  source_change_impact_id
  status          -- still_valid, needs_review, needs_repair, retire
  reason
  suggested_patch_json
  assessed_by     -- system, agent, human
  created_at
```

```text
concept_impact_assessments
  id
  concept_id
  source_change_impact_id
  status          -- unaffected, expanded, narrowed, contradicted, obsolete
  mastery_effect  -- none, review_due, confidence_down, reset_learning
  reason
  created_at
```

## Mastery Effects

A source change should not automatically reset mastery.

Instead, source impact should adjust confidence and scheduling based on severity:

```text
expanded     -> schedule review or lower coverage confidence
narrowed     -> review affected questions and reduce confidence slightly
contradicted -> mark review due and reduce confidence substantially
obsolete     -> retire questions and move concept back toward learning
```

Add a review reason code:

```text
source_changed
```

The UI should be able to say:

```text
Review due because your source material changed, not because you forgot.
```

## Repair Workflow

Question repair should receive narrow context instead of an entire source document:

```json
{
  "question_id": 123,
  "old_source_excerpt": "...",
  "new_source_excerpt": "...",
  "question_text": "...",
  "current_answer": "...",
  "concept": "S3 storage classes",
  "task": "Decide whether the question is still valid. If not, patch it."
}
```

This should make repairs cheaper, safer, and more accurate.

## Generation Workflow

Source changes can create targeted generation work:

```text
New span -> affected concept -> missing cognitive level -> generate question
```

Example:

```text
Source change added a new constraint under "S3 Intelligent-Tiering."
Current bank has 6 questions for the concept, but none test the new constraint.
Generate 3 application questions grounded in the new span.
```

This is more useful than generic "generate more questions for this concept."

## Minimal Future V1

The smallest useful version:

1. Segment source snapshots into heading-based `source_spans`.
2. Let generated questions reference `source_span_id` and `source_snapshot_id`.
3. On re-ingest, hash-match old and new spans.
4. Mark linked questions stale when their source span changed or disappeared.
5. Surface affected concepts.
6. Recommend repair or new question generation.

V1 statuses:

```text
source span: unchanged / changed / removed / new
question: current / source_changed / source_removed / needs_repair
concept: unaffected / source_changed / needs_coverage
```

V1 UI:

```text
Source changed -> affected questions -> repair queue
Source changed -> affected concepts -> generate missing coverage
```

## Non-Goals For The First Version

- Full semantic contradiction detection.
- Automatic mastery resets.
- Fully automated question repair without human or agent review.
- Perfect span identity across arbitrary rewrites.
- Multi-source question citation modeling unless current question generation needs it.

## Acceptance Criteria

- Source snapshots can be segmented into deterministic source spans.
- Generated questions can record source-span grounding.
- Re-ingesting changed content identifies changed, removed, and new spans.
- Questions linked to changed or removed spans are marked for review.
- Concepts linked to changed or new spans surface coverage impact.
- Repair context can include old and new source excerpts.
- Source impact can create targeted generation recommendations.
- Mastery review can be scheduled with reason `source_changed`.

