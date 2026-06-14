# Human-in-the-Loop Concept Stewardship

## Decision

Quizzy should treat concepts as a maintained subject-scoped learning map, not as passive generation
tags. Agents can propose taxonomy changes and generate questions, but the application owns current
taxonomy state, approval rules, mastery rollups, and the durable history of stewardship decisions.

This feature should be implemented as one complete feature branch. The app is local and has no
legacy compatibility requirement, so implementation can use a fresh database reset and move directly
to structured concept proposals instead of preserving flat `concept_names` APIs.

## Problem

Question generation can create concept entropy. Agents may turn protocol variants, source headings,
thresholds, one-off facts, or question titles into separate concepts even when those labels are only
details under a broader learning objective.

Prompt guidance helps, but it cannot guarantee a clean taxonomy. Quizzy needs a workflow where
concept stewardship is part of studying: visible from mastery, actionable from the subject workspace,
and reusable by future generation.

## Domain Model

Subjects are broad study namespaces such as AWS, cardiology, world history, or organic chemistry.
Concepts are subject-scoped learning objectives. A concept may be broad enough to drive a large
focused study session, and narrower concepts can sit under broader parent concepts.

The concept hierarchy is a tree:

- A concept has at most one parent concept.
- Cross-cutting overlap is represented by assigning a question to multiple concepts, not by giving a
  concept multiple parents.
- Preferred depth is no more than three concept levels. Deeper structures are unusual and should be
  treated as review-worthy.
- Concept names remain unique and self-contained inside a subject, even when the UI renders compact
  display labels under a parent.

Concept status:

- `active`: visible in ordinary mastery, practice, MCP generation context, and question assignment.
- `proposed`: visible in stewardship review, but hidden from ordinary mastery and practice.
- `archived`: preserved for history, but hidden from ordinary mastery and practice and unavailable
  for new ordinary assignments.

Questions should be assigned to the most specific applicable concept or concepts. Parent concepts
receive signal through rollup, so generated questions should not duplicate broad parent assignments
when a child assignment is sufficient.

An active question must have exactly one primary concept assignment. Secondary assignments are
allowed for genuine cross-concept questions, but generated questions should default to at most two
total concept assignments: one primary and at most one secondary.

## Assignment And Approval

Generated question creation should accept structured concept proposals instead of flat concept-name
lists. The minimum proposal shape is:

- `name`: proposed canonical concept name.
- `parent_name`: optional proposed parent concept.
- `role`: `primary` or `secondary`.
- `rationale`: required for new concepts or new hierarchy placements, optional for exact existing
  concept matches.

All generated concept proposals go through app-owned concept assignment logic before they become
active taxonomy. The app should resolve only conservative deterministic matches:

1. Normalize safe surface differences such as whitespace, punctuation, casing, plural forms, and
   subject-name prefixes.
2. Resolve exact active concept matches within the same subject.
3. Resolve exact active alias matches within the same subject.
4. Apply subject-owned deterministic vocabulary rules such as known acronyms or curated synonyms.
5. Return review candidates for fuzzy, token-overlap, or near-duplicate matches instead of silently
   merging them.

MCP-driven generation must not create new active concepts or active hierarchy placement. MCP agents
may reuse exact active concept or alias matches, but new concepts are saved as proposed taxonomy and
the affected questions remain draft until the user reviews them in the app-owned stewardship UI.
This avoids treating agent-provided MCP elicitation responses as human approval.

Unresolved proposed concept assignments should keep affected questions out of ordinary practice and
mastery until resolved. Use broad question lifecycle state such as `draft` plus a specific review
reason like unresolved concept assignment.

## MCP Workflow

The generation flow should be:

1. Agent calls a generation-context tool for the subject and optional source document.
2. App returns the active concept tree, compact relevant aliases or ledger guidance, source context,
   and concept policy.
3. Agent drafts questions and a structured concept plan.
4. Agent calls `prepare_concept_plan` to resolve exact matches and surface new concepts, proposed
   hierarchy placements, conflicts, and near-duplicate warnings.
5. Agent calls a creation tool that saves unresolved taxonomy as proposed concepts and draft
   questions, then returns a direct subject review link for the user.
6. User reviews proposed concepts, placement, assignment, aliases, and rationale in the app-owned
   stewardship workflow. Approval records durable decisions in the concept decision ledger.

Non-interactive generation should use a proposed-state workflow instead of silently creating active
taxonomy.

MCP should also expose mastery read tools:

- A concise focus tool for weak or improving concepts that need attention.
- A full subject mastery tree tool for context-rich agent workflows.

Both tools should use the same mastery read model as the web UI.

## Concept Decision Ledger

Every approved stewardship action should create durable subject-scoped decision history. The concept
decision ledger is append-only history; current behavior reads from current taxonomy state such as
concept rows, parent placement, status, aliases, and review items.

Record decisions for:

- concept creation and approval
- hierarchy placement
- merge
- split
- rename
- alias creation
- alias forgetting
- archive
- delete
- question reassignment
- proposed concept rejection

Aliases are current taxonomy memory, not normal concept-list content. They should resolve future
proposals internally, but should not be dumped into ordinary MCP context. MCP context should expose
only compact, relevant taxonomy guidance.

Alias rules:

- Renaming the same concept identity creates an alias by default.
- Merging a source concept into a target creates an alias by default.
- Moving a question assignment does not create an alias.
- Moving a concept to a different parent does not create an alias.
- Correcting hallucinated placement does not create an alias.
- An alias must not shadow an active concept name in the same subject.
- Users can forget aliases when they no longer help future assignment.

## Target Data Shape

The implementation should make the taxonomy model explicit instead of layering this feature onto the
existing flat concept helper.

Concept current state:

- `concepts.status`: `active`, `proposed`, or `archived`.
- `concepts.parent_concept_id`: nullable self-reference for the one-parent concept tree.
- `concepts.normalized_name`: deterministic matching key separate from display `name` and route
  `slug`.
- Subject-wide uniqueness remains based on canonical concept identity. Sibling-only duplicate names
  are not allowed.

Question assignment current state:

- `question_concepts.role`: `primary` or `secondary`, replacing boolean-only primary semantics.
- `question_concepts.rationale`: optional rationale from the approved concept proposal.
- Exactly one active primary assignment is required for an active question.

Alias current state:

- `concept_aliases.subject_id`
- `concept_aliases.alias_normalized_name`
- `concept_aliases.target_concept_id`
- active/inactive or forgotten state
- optional source decision event id

Decision history:

- `concept_decision_events` should record append-only stewardship decisions with subject, action,
  affected concept ids, optional question ids, rationale, actor/source, and structured metadata.
- Current alias, concept status, and parent placement tables drive app behavior. Do not query the
  event ledger as the live resolution table.

Review state:

- Concept stewardship review should be distinct from question health review. Existing
  `question_review_items` can remain for question quality, but concept proposals, hierarchy
  conflicts, duplicate candidates, and alias conflicts need concept-oriented review state.

Important invariants:

- Prevent concept cycles.
- Enforce one active primary concept assignment per active question.
- Prevent alias names from shadowing active concept names in the same subject.
- Do not allow ordinary assignments to archived concepts.
- Do not allow active questions to depend on unresolved proposed concept assignments.

Useful indexes for the target model:

- `concepts(subject_id, status)`
- `concepts(subject_id, parent_concept_id)`
- `concepts(subject_id, normalized_name)`
- `concept_aliases(subject_id, alias_normalized_name)`
- `question_concepts(concept_id, question_id)`
- `question_concepts(question_id, role)`
- `attempt_answers(question_id)`
- `questions(subject_id, status)`

## Mastery Read Model

Mastery should be computed from current taxonomy and attempt evidence by a live SQL-backed read
model, not persisted summary rows.

The read model should:

- Compute hierarchy-aware rollups from active concepts, question assignments, and attempt answers.
- Include direct parent questions and descendant concept questions in parent mastery.
- Include both primary and secondary assignments in mastery evidence.
- Preserve role-aware evidence counts so the UI can distinguish primary weakness from secondary
  cross-concept evidence.
- Be callable from both web routes and MCP tools.

Persisted mastery summaries can be added later if measured performance requires caching. The initial
implementation should replace ORM-heavy mastery traversal with a dedicated read model instead of
introducing invalidation-heavy summary tables.

## Practice Behavior

Selecting a parent concept for practice includes questions assigned directly to the parent and
questions assigned to descendant concepts.

Practice intents:

- Quick practice: broad mixed practice from a subject or concept tree, balanced enough to avoid one
  child concept dominating.
- Concept drill: focused practice for a selected concept tree.
- Mastery review: weakness-weighted practice based on mastery evidence.
- Random practice: intentionally random practice inside a selected subject or concept tree, with
  light guardrails against extreme skew.
- Source practice: practice from questions associated with a specific source document or source
  collection.

Concept-tree practice should consider questions eligible when any primary or secondary assignment is
inside the selected subtree. Selection balance should primarily use primary concept coverage.
Secondary-only matches should be sprinkled in as cross-concept reinforcement and should not dominate
the session.

## Human Stewardship UI

Mastery remains the discovery surface because taxonomy problems show up there. The subject detail
page should be the primary concept stewardship workspace.

The subject workspace should include:

- a compact concept tree
- rollup accuracy
- linked question counts
- open review counts
- quick practice actions
- stewardship actions in an overflow menu
- review signals for concept stewardship and question review

Default concept rows should show only the information needed for common decisions:

- concept display label
- non-active status when relevant
- rollup accuracy
- linked question count
- open review count when relevant
- practice action
- overflow menu

Use progressive disclosure for lower-frequency detail such as source documents, generation batches,
direct-vs-descendant counts, aliases, recent ledger decisions, and linked-question tables. Detail can
live in expandable sections, drawers, or modals so the mobile experience stays compact.

Stewardship signals should be persistent and contextual:

- global nav or home badge for total review count
- subject-level badge for subject review count
- post-generation callout when generated output needs approval or review

Avoid generic toast-style nagging.

## Stewardship Actions

Supported actions:

- approve proposed concepts and hierarchy placements
- merge concepts
- rename concepts
- split a concept into child concepts
- move a child concept to a different parent
- reassign selected questions
- archive concepts
- delete empty/history-free concepts or subtrees
- forget aliases
- reject proposed concepts

Merge:

- Moves source concept question assignments to the target concept.
- Rehomes source children under the target unless the user chooses otherwise.
- Makes the source concept inactive.
- Creates alias/redirect memory by default.
- Does not expose alias memory in normal concept lists.

Split:

- Creates or selects child concepts under the original concept.
- Moves selected direct question assignments into those children.
- Keeps the original concept active as the parent bucket unless the user explicitly archives it.

Archive:

- Preserves history while removing concepts from ordinary mastery and practice.
- Archiving a parent concept requires an explicit child-handling choice:
  - archive the subtree
  - promote children
  - move children elsewhere
  - cancel

Delete:

- Reserved for empty or accidental concepts/subtrees.
- Should be limited to concepts with no meaningful assignments, children with history, aliases, or
  stewardship history.

Safe previews are required for:

- merge
- split
- archiving a parent concept
- deleting a concept or subtree
- moving a child concept to a different parent
- bulk question reassignment
- rejecting a proposed concept that affects draft questions

Lightweight confirmation is enough for simple renames, forgetting aliases, approving exact alias
resolution, and dismissing stale suggestions.

Review items should close automatically when their underlying condition is no longer true. The
ledger records the decision that resolved the review work.

## Current Code Impact

The current codebase is still built around flat concepts and should be reshaped rather than extended
in place.

Replace flat concept creation paths:

- `app.services.quiz_service.get_or_create_concepts` currently creates canonical concepts directly
  from strings. It should not remain the concept creation path for generated questions.
- `app.services.question_bank_service._build_question` currently calls `get_or_create_concepts` and
  builds `QuestionConcept` links from `concept_names`. Replace this with structured concept proposal
  resolution.
- `app.services.generation_service.GeneratedQuestionInput` and `app.schemas.QuestionCreate` still
  expose `concept_names`. Generated/MCP paths should move to structured proposals; local web forms
  may use a separate user-facing path that resolves through the same assignment service.

Replace current concept maintenance behavior:

- `app.services.concept_service.merge_concepts` currently moves links and hard-deletes source
  concepts. This must be replaced with ledger-backed merge semantics: move assignments, rehome or
  explicitly handle children, deactivate/archive the source, and create alias memory by default.
- Existing `suggest_concept_merges` can inspire review candidates, but fuzzy/token overlap must
  produce review suggestions only, not automatic merges.

Replace mastery traversal:

- `app.services.grading_service.get_mastery_rows` currently loads concepts, questions, and attempts
  through ORM relationships. Replace it with a SQL-backed mastery read model that understands concept
  hierarchy, active/proposed/archived status, and primary/secondary assignment roles.
- `app.services.subject_service`, `app.routes.web`, `app.routes.api`, and MCP mastery tools should
  call that read model instead of reimplementing aggregation.

Update practice selection:

- `app.services.practice_service.assemble_concept_drill` currently filters exact concept ids only.
  It must expand selected concepts to descendants and include secondary-only matches as limited
  cross-concept reinforcement.

Update MCP surfaces:

- `app.mcp.server.GENERATION_GUIDANCE`, `CONCEPT_POLICY`, `GeneratedQuestionInput`, and
  `create_generated_questions` should use structured concept proposals, `prepare_concept_plan`, and
  an MCP creation tool that leaves new taxonomy in proposed review state.

Update UI surfaces:

- `/mastery` remains the discovery surface.
- `/subjects/{subject_id}` is the stewardship workspace.
- `app/templates/mastery/index.html`, `app/templates/subjects/detail.html`,
  `app/templates/quizzes/new.html`, and question metadata partials need status-aware concept display
  and review signals.

Update tests intentionally:

- Tests and fixtures currently use `concept_names` heavily. Because this app has no legacy
  compatibility requirement, update tests to the structured concept proposal contract instead of
  adding compatibility assertions.

## Acceptance Criteria

- Generated question creation uses structured concept proposals, not flat `concept_names`.
- MCP can prepare a concept plan and save proposed concepts plus draft generated questions,
  returning a direct subject review link for user approval.
- Concepts support active, proposed, and archived status.
- Concepts support a one-parent hierarchy with soft three-level guidance.
- Active questions have exactly one primary concept assignment.
- Generated questions default to at most one secondary concept assignment.
- Mastery rollups include descendant concepts and role-aware primary/secondary evidence.
- Subject detail includes a compact concept stewardship panel with review signals.
- User can merge, rename, split, archive, move child concepts, reassign questions, forget aliases,
  reject proposed concepts, and delete only empty/history-free concepts or subtrees.
- Practice by parent concept includes descendant concepts and uses balanced selection.
- MCP exposes a concise mastery focus tool and a full subject mastery tree tool.
- No legacy flat concept-name compatibility is carried solely for existing local data.
