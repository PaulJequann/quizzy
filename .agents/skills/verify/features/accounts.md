# Study sign-in and privacy

Each person signs in to a private library. The reviewer can choose a learner workspace.

## Sub-features

- accounts.login accepts fixture credentials and rejects invalid credentials.
- accounts.private hides reviewer materials and learner drafts from the learner.
- accounts.switch lets the reviewer select their own or the learner workspace.
- accounts.logout removes access to private pages.

## How to get to it (user POV)

Open `/login`. Reviewer navigation has **Switch workspace**. Both accounts have **Sign out**.
The root `/` also leads to the quiz list after sign-in.

## Driving it with T3 preview

Preconditions: fresh launcher and successful doctor. Use the manifest URL.

- Open `/login`; type `learner@example.com` into `input[name="username"]` and
  `Verify-Learner-2026` into `input[name="password"]`. Click `role=button[name='Sign in']`.
  Require `/quizzes`, **Learner practice**, and no **Parent private study** or unpublished draft.
- Read rendered links. Require **My quizzes**, **My progress**, **My results** and no
  **Switch workspace**, **Subjects**, or **Prepare next practice**.
- Navigate to `/quizzes/PARENT_PUBLISHED_ID/take` and `/quizzes/LEARNER_DRAFT_ID/take`
  using the manifest IDs. Require **Quiz not found** for both.
- Click `role=button[name='Sign out']` from `/quizzes`. Require `/login`; reopening
  `/quizzes` must require sign-in.
- Repeat login with reviewer credentials. Click `role=link[name='Switch workspace']`.
  Click `form:has(h2:text-is("Learner")) button`. Require **Reviewer preview** in the header.
  Reopen `/accounts` and select `form:has-text("Own study") button`; require reviewer material.
- For failed login, sign out and enter an incorrect password. Require
  **Email or password was not accepted.** and remain on `/login`.

## Gotchas

Keep `127.0.0.1` consistent with the manifest. Substituting `localhost` causes mutation
403 responses. An inactive account is not a bad-origin failure. Do not use production
credentials. A logged-in reviewer is not evidence of the learner's isolation.
