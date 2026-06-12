# Human-in-the-Loop Concept Stewardship

## Problem

Question generation can create concept entropy. Agents may create separate concepts for protocol
variants, thresholds, implementation details, question titles, or source-section headings even when
those labels are all examples of one durable learning objective.

Prompt guidance can reduce this, but it cannot guarantee a clean taxonomy. Quizzy needs a human
review workflow where concept cleanup is a natural part of studying and maintaining the question
bank.

## Desired State

Concepts should be editable learning-map objects, not permanent side effects of generation.

The user should be able to:

- Merge duplicate or overly narrow concepts.
- Rename a concept without losing question history.
- Move questions from one concept to another.
- Split a noisy concept into two better concepts.
- Archive concepts that should no longer appear in mastery.
- Review which questions are attached to each concept before making changes.
- See source documents and generation batches that introduced a concept.

## Natural Workflow

Mastery should remain the entry point because that is where taxonomy problems become visible.

When a subject row looks wrong, the user can open a subject-level concept management view:

- `/mastery/{subject_slug}` or a management panel from the existing mastery page.
- Shows every concept for that subject with attempts, accuracy, linked question count, source
  coverage, and generation-batch provenance.
- Highlights likely cleanup candidates: low-use concepts, near-duplicate names, many concepts from
  one source, and concepts whose questions overlap heavily with another concept.

From there, the user can select concepts and take actions:

- Merge into an existing concept.
- Rename concept.
- Move selected questions to another concept.
- Create a new concept from selected questions.
- Archive empty or obsolete concepts.

## Ownership

### App Responsibilities

- Own the canonical concept taxonomy.
- Persist concept edits, merges, archives, and question-concept link changes.
- Preserve question attempt history when concepts are merged or renamed.
- Show provenance for every concept and question assignment.
- Make cleanup discoverable from mastery instead of hiding it behind MCP-only tools.
- Provide safe previews before destructive taxonomy edits.

### Agent / MCP Responsibilities

- Suggest candidate merges, renames, and question moves.
- Explain why a concept looks too narrow or duplicate-like.
- Prepare review context: linked questions, source excerpts, attempts, and similar concepts.
- Execute user-approved taxonomy changes through app-owned tools.

Agents should not silently decide canonical taxonomy during normal generation. They can propose and
assist, but the user owns final concept stewardship decisions until the app has stronger review
queues and confidence signals.

## First Useful Version

1. Add a subject mastery detail page.
2. Show concepts with question counts, attempts, accuracy, and source/generation provenance.
3. Add merge and rename actions.
4. Add a question reassignment table for one concept at a time.
5. Add MCP tools that return suggested cleanup candidates, but require explicit user action to
   apply changes.

## Later

- Concept aliases so old labels map to canonical concepts.
- Concept merge event history.
- Review queue items for generated batches that create too many concepts.
- Proposed concepts hidden from mastery until approved.
- Batch-level concept planning UI before generated questions are persisted.
