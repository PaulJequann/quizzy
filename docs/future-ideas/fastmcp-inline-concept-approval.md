# FastMCP Inline Concept Approval

## Problem

The concept stewardship branch supports active concept creation through an explicit
`approve_new_concepts=true` tool argument, but it does not yet use FastMCP elicitation to ask the
user for approval inside the tool call.

That means the non-blocking path exists, but the best user experience is not fully wired:

- Agents can prepare a concept plan.
- Agents can create active concepts when approval is already represented by the tool arguments.
- Agents can save proposed concepts and draft questions when approval is unavailable.
- Agents cannot yet have the creation tool present the plan and ask the user inline before writing
  active concepts.

## Desired Experience

For MCP-driven generation, the user should not have to choose between unsafe automation and
multi-step manual review. The creation workflow should be able to pause at the exact moment new
taxonomy would be created, show the proposed concept plan, and ask for approval.

The happy path should feel like:

1. Agent ingests or reads source material.
2. Agent drafts questions and structured concept proposals.
3. Agent calls `prepare_concept_plan`.
4. Agent calls an eliciting creation tool.
5. Quizzy presents the new concepts, hierarchy placements, and rationale.
6. User approves or declines inline.
7. If approved, Quizzy creates active concepts and active questions in one flow.
8. If declined or unsupported, Quizzy can intentionally save draft/proposed review work instead.

## Product Intent

The goal is not to block capable agents from creating useful taxonomy. The goal is to prevent silent
taxonomy mutation while preserving a low-friction automation path.

Approved agent-created concepts are acceptable when:

- the agent provides structured concept proposals,
- new concept or parent creation includes rationale,
- the user approves the plan through MCP elicitation or an equivalent trusted UI action,
- the decision is recorded in the concept decision ledger,
- later UI stewardship can rename, move, split, merge, archive, or reject mistaken taxonomy.

Human review should be strongest for app-detected uncertainty:

- fuzzy or token-overlap near matches,
- duplicate candidates,
- suspiciously granular one-off concepts,
- hierarchy conflicts,
- proposed concepts created when inline approval was unavailable.

## Implementation Sketch

Add eliciting variants or upgrade the existing write tools:

- `create_generated_questions`
- `add_questions_to_quiz`
- `create_generated_quiz_with_questions`

When a request includes new active concepts and `approve_new_concepts` is false:

1. Build the concept plan internally from the submitted proposals.
2. If all proposals resolve to existing active concepts or aliases, proceed without elicitation.
3. If new concepts or placements are present, call `ctx.elicit(...)` with a compact approval schema.
4. On accept, create active concepts/questions and ledger events.
5. On decline/cancel, return a clear non-write result or save draft/proposed output only when the
   caller explicitly requested that fallback.
6. If the MCP client does not support elicitation, fail closed for active creation and explain that
   `save_unresolved_as_draft=true` is available.

The existing `approve_new_concepts` flag can remain as a low-level explicit approval path for tests
or trusted internal callers, but ordinary agent guidance should prefer elicitation.

## Acceptance Criteria

- MCP write tools can ask the user to approve new active concepts inline.
- Active concept creation does not require a separate manual UI step when the user approves in MCP.
- Clients without elicitation support do not silently create active taxonomy.
- Declined approvals do not partially create active concepts.
- Approved concept creation records rationale and decision ledger entries.
- Proposed/draft fallback remains available as an intentional workflow.
