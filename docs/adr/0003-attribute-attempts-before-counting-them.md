# Attribute attempts before counting them

Quizzy records the authenticated user on each quiz attempt and exam submission. The stored
`actor_role` captures whether the user was a learner or a test-taking administrator at submission
time. Answer rows inherit that attribution from their attempt. A workspace identifies where a
record belongs; it does not prove who answered.

An administrator's attempt in their own workspace is a test attempt. A reviewer previewing
another user's workspace cannot submit. A non-administrator working in their own workspace is a
learner. These rules use account and session state, never workspace IDs or names. If an
administrator needs personal learner progress, the app needs an explicit study mode or a separate
learner account.

Mastery, practice selection, recent misses, question performance, and review scheduling use
learner attempts. History and results keep every role visible. Exam submission saves the actor
before grading; release copies that actor into the resulting attempt and records the reviewer
separately.

The attribution migration marks existing attempts and submissions `unknown`, with no actor ID.
Neither workspace ownership, exam assignment author, nor reviewer identifies the submitter. The
migration preserves answers, scores, and timestamps. It resets derived review schedules and
performance-based question health because those values may include unknown attempts. Explicit
question flags and archived states remain. Existing review items remain as historical records;
review their evidence before treating an old conclusion as current learner performance.

The [live mastery read model](0002-compute-mastery-with-live-sql-read-model.md) applies the learner
filter before aggregating answers. It still includes questions with no learner answers in coverage
calculations. MCP mastery responses state that policy, and `list_attempts` exposes the excluded
history with an optional role filter.
