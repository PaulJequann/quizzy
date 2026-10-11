# Authoring and publication

The reviewer prepares quizzes in the learner library and publishes them for study.

## Sub-features

- authoring.private separates each account's draft library.
- authoring.publish makes an existing draft available to the learner.
- authoring.agent keeps MCP-generated quizzes as drafts for reviewer review.

## How to get to it (user POV)

Reviewer **Switch workspace**, **Quizzes**, then the draft quiz detail. Browser **Practice**
opens `/quizzes/new`. Agents use MCP with an explicit destination workspace.

## Driving it with T3 preview

Preconditions: reviewer sign-in, learner workspace selected. Seeded drafts are empty.

- Open `/quizzes/LEARNER_DRAFT_ID` from the manifest. Click `role=link[name='Add questions']`.
  Type `What does verification prove?` into `textarea[name="stem"]`. Fill
  `input[name="choice_1"]` through `input[name="choice_4"]` with `Behavior`, `Color`,
  `Luck`, and `Nothing`. Choose `input[name="correct_choice"][value="0"]` and click
  `role=button[name='Add question']`. Save the values and controls used.
  Require the new question on the detail page after a reload. Creating fixture rows
  does not count as proof of this authoring step.
- Click `role=button[name='Publish']` after the page and HTMX are ready. Require
  **Published** on detail, then sign in as learner and require this quiz in `/quizzes`.
- Use **Start quiz** for the newly published quiz and require the authored question.
- For MCP changes, run `make test-db`; inspect `tests/test_family_mcp.py` for actual
  fresh-process tool calls, draft enforcement, and explicit workspace arguments.
  Record MCP as separate coverage rather than claiming it from browser publication.

## Gotchas

An empty draft cannot establish the full authoring flow. Inspect the live form before
typing. Confirm the new question on the detail page even if the authoring page's count is stale.
Browser reviewer practice generation can publish directly; MCP generation stays draft.
