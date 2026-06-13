# Quizzy

Quizzy is a personal study system for building reusable question banks and tracking mastery across
subject-scoped learning objectives.

## Language

**Subject**:
A broad study namespace that groups related questions, concepts, source material, and practice.
Examples include AWS, cardiology, world history, or organic chemistry.
_Avoid_: Topic, category

**Concept**:
A subject-scoped learning objective that can drive mastery signal and practice across multiple
questions. Concepts may be arranged in a one-parent hierarchy, where broader parent concepts act as
selectable study buckets and narrower child concepts support deeper drills. Concept names should be
unique and self-contained within a subject.
_Avoid_: Tag, source heading, question title

**Concept display label**:
A compact presentation of a concept name when hierarchy already provides context. The canonical
concept name remains self-contained even when the UI de-emphasizes repeated parent wording.
_Avoid_: Canonical short name

**Child concept**:
A narrower concept under a broader parent concept. A child concept is still a concept, not a
separate kind of learning object.
_Avoid_: Subconcept, detail, fact, variant

**Concept tree**:
The one-parent hierarchy of concepts inside a subject. Cross-cutting overlap is represented by
assigning a question to multiple concepts, not by giving a concept multiple parents. The preferred
depth is no more than three concept levels, with deeper structures treated as unusual.
_Avoid_: Concept graph, multiple-parent hierarchy

**Mastery**:
The learner's performance signal for a concept based on question attempts. Parent concept mastery
rolls up attempts from questions directly assigned to the parent and from descendant concepts.
_Avoid_: Score, grade

**Concept assignment**:
The relationship between a question and the most specific concept or concepts the question tests.
Parent concepts are included through rollup, not duplicate assignment, unless the question is
genuinely broad enough to test the parent concept directly. An active question has exactly one
primary concept assignment.
_Avoid_: Tagging everything, duplicating parent labels

**Concept placement**:
The position of a concept in the subject's concept hierarchy. Agents may propose parent-child
placement, but new parent creation and uncertain placement require human approval.
_Avoid_: Automatic hierarchy inference

**Concept proposal**:
A structured recommendation for a concept assignment or placement, including the proposed concept
name, optional parent concept, role in the question, and rationale.
_Avoid_: Flat concept name

**Concept approval**:
Explicit user acceptance of a proposed concept creation or hierarchy placement. MCP-driven creation
of new active concept hierarchy requires elicited approval unless it resolves to existing taxonomy
memory.
_Avoid_: Prompt-only permission, silent hierarchy creation

**Concept merge**:
A stewardship action that moves concept assignments from a source concept to a target concept and
usually creates internal alias or redirect memory from the old name to the target. Merge memory is
used for resolution but is not shown in normal concept context by default.
_Avoid_: Hard delete, context dump

**Concept split**:
A stewardship action that refines a broad concept by creating or selecting child concepts and moving
selected direct question assignments into those children. The original concept usually remains active
as the parent bucket.
_Avoid_: Replacing the parent concept

**Concept alias**:
An alternate concept name that resolves to a canonical concept. Aliases are internal taxonomy memory
by default and may be pruned when they no longer help future assignment. Aliases come from identity
preserving renames or merges, not from correcting mistaken question assignment or concept placement;
an alias should not shadow an active concept in the same subject.
_Avoid_: Duplicate concept

**Archived concept**:
A concept removed from ordinary mastery and practice selection while preserving history for review.
Archiving is preferred over deletion when the concept has meaningful assignments or attempts.
Archiving a parent concept requires an explicit child-handling choice: archive the subtree, promote
children, or move children elsewhere.
_Avoid_: Deleted concept

**Deleted concept**:
A concept record removed because it was an empty or accidental mistake. Deletion is rare and should
be limited to concepts or subtrees with no meaningful assignments, children with history, aliases,
or stewardship history.
_Avoid_: Archive

**Forgotten alias**:
An alias or redirect memory intentionally removed so it no longer influences future concept
assignment. Forgetting an alias does not undo historical stewardship actions.
_Avoid_: Undo merge

**Concept decision ledger**:
The append-only history of approved concept stewardship decisions. The ledger explains what happened
and why; current taxonomy behavior comes from current concept, placement, alias, and review state.
_Avoid_: Current alias table, mutable taxonomy state

**Proposed concept**:
A concept or hierarchy placement saved for later stewardship because it was not approved as active.
Proposed concepts are intentional review state, not a silent fallback for failed active creation.
_Avoid_: Half-created concept

**Unresolved concept assignment**:
A proposed relationship between a question and concept taxonomy that has not yet been approved.
Questions with unresolved concept assignments should stay out of ordinary practice and mastery until
the assignment is resolved.
_Avoid_: Active question with hidden taxonomy

**Question review reason**:
The specific reason a question needs attention while its lifecycle status remains broad, such as an
unresolved concept assignment.
_Avoid_: One status per problem type

**Concept practice**:
Practice focused on one or more selected concepts. Selecting a parent concept includes questions
assigned directly to that concept and questions assigned to descendant concepts. Questions where the
selected concept tree appears as secondary evidence may be included, but primary assignments define
the main coverage balance.
_Avoid_: Exact-tag-only drill

**Quick practice**:
Broad mixed practice from a subject or concept tree, balanced enough to avoid over-representing a
single child concept.
_Avoid_: Random practice

**Mastery review**:
Practice weighted toward weak or recently missed concepts based on mastery evidence.
_Avoid_: Remediation quiz

**Random practice**:
Intentionally random practice within a selected subject or concept tree, with only light guardrails
against extreme skew.
_Avoid_: Quick practice

**Source practice**:
Practice drawn from questions associated with a specific source document or source collection,
regardless of which concepts those questions touch.
_Avoid_: Concept practice

**Stewardship signal**:
An app-visible indication that concept or question taxonomy needs attention. Stewardship signals
should guide the learner toward review work without replacing ordinary study flows. Signals should
be persistent and contextual, such as nav badges, subject badges, or post-generation callouts, not
generic toast-style nagging.
_Avoid_: Hidden review backlog, notification spam

**Concept stewardship review**:
Review work for proposed concepts, proposed hierarchy placement, duplicate candidates, or taxonomy
conflicts. Batch and alias issues are evidence for concept stewardship review, not separate top-level
review buckets.
_Avoid_: Generation review

**Question review**:
Review work for questions that need attention, including draft questions blocked by unresolved
concept assignment or questions flagged by quality evidence.
_Avoid_: Concept stewardship review

**Review resolution**:
The closure of review work when the underlying condition has been fixed or intentionally dismissed.
Review items should close from state changes rather than requiring duplicate manual cleanup.
_Avoid_: Stale review item

## Example Dialogue

Developer: "Should AWS be a concept?"

Domain expert: "No, AWS is the subject. IAM can be a parent concept because I may want a full
practice session across IAM. IAM permissions can be a child concept when I want to drill deeper."

Developer: "If I drill S3, should that include S3 encryption questions?"

Domain expert: "Yes. S3 mastery is the overall rollup across direct S3 questions and child concepts
like S3 encryption, storage classes, and bucket policies."

Developer: "Should an S3 encryption question also be assigned to S3?"

Domain expert: "No. Assign it to S3 encryption. S3 sees it through the concept hierarchy. Assign
directly to S3 only when the question is broad S3 knowledge rather than a child concept."

Developer: "Can the agent create S3 as a parent for S3 encryption?"

Domain expert: "The agent can propose that hierarchy, but Quizzy should treat new parent creation
or uncertain placement as a human-approved stewardship decision. In MCP workflows, that approval can
happen through elicitation during tool execution."

Developer: "If the MCP client cannot ask me for approval, should active hierarchy creation continue?"

Domain expert: "No. Active creation should fail closed. Saving proposed concepts for later review is
a separate intentional workflow."

Developer: "Should generated questions submit flat concept names?"

Domain expert: "No. They should submit concept proposals so Quizzy can preserve the intended
concept, parent placement, role, and rationale."

Developer: "If I select S3 for practice, do I only get questions tagged directly to S3?"

Domain expert: "No. Selecting S3 means practicing the S3 concept tree, including direct S3 questions
and child concepts like S3 encryption and storage classes."

Developer: "What is the difference between quick S3 practice and S3 mastery review?"

Domain expert: "Quick practice should give broad coverage across the S3 concept tree. Mastery review
should stay inside S3 but weight the session toward weak or missed child concepts."

Developer: "Where do I clean up concepts?"

Domain expert: "Mastery can show that cleanup is needed, but the subject detail page is the concept
stewardship workspace."
