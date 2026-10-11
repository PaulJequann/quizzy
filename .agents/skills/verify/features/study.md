# Taking a quiz

The learner answers a published quiz and gets saved results with the original answers.

## Sub-features

- study.take opens a published quiz and submits answers.
- study.grade displays the score and correct answer.
- study.feedback saves the learner's retrieval feedback.

## How to get to it (user POV)

Learner **My quizzes** has **Start quiz**. Results open after Submit and from **My results**.
Reviewer quiz detail has a take link, but reviewer preview cannot submit for the learner.

## Driving it with T3 preview

Preconditions: learner sign-in from accounts.md, fresh seeded published quiz.

- Click `role=link[name='Start quiz']`. Require **What does pytest run?**.
- Click `label:has-text("Backups")`. Inspect the revealed controls, then click
  `role=button[name='Submit']`. Require a results route, score **0/1**, selected
  **Backups**, and correct **Tests**. Save the actual results URL.
- Click `role=button[name='1 Shaky']`. Require **Recorded: Shaky**. Reopen the results
  URL to confirm the attempt persists, then open **My progress** and require
  **Last retrieval felt shaky**.
- Click `role=link[name='My results']`, then `role=link[name='Review results']`.
  Require the same recorded answer.

## Gotchas

The Submit button is initially hidden until an answer is chosen. Wait for navigation
after submission. Feedback is JavaScript JSON, so a form-only API test is insufficient.
Use a fresh run when checking the exact first-attempt score and due state.
