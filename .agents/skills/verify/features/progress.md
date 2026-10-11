# Progress and reviewer oversight

The learner sees their own history and mastery. The reviewer can inspect it without adding attempts.

## Sub-features

- progress.history lists recorded attempts and links results.
- progress.mastery shows concept evidence and review state.
- progress.preview prevents parent submissions and learner feedback writes.

## How to get to it (user POV)

Learner navigation has **My results** and **My progress**. Reviewer navigation has
**Practice history** and **Mastery** in the selected workspace.

## Driving it with T3 preview

Preconditions: complete study.md, then retain its result URL.

- As learner, open `/progress`; follow the attempt link and confirm **Backups** and **Tests**.
  Open `/mastery` and inspect the recorded concept evidence. No admin forms should appear.
- Sign out, sign in as the reviewer, and select **Learner** on `/accounts`.
  Open `/progress` and the same results URL. Require the learner attempt and zero
  `form[action*="retrieval-feedback"]` or issue-report forms.
- Open the learner published quiz's take route. Require **Reviewer preview** and disabled
  `[data-submit-button]`. Do not use JavaScript to force submission.
- Select **Own study** and open `/progress`. Require no learner attempt.

## Gotchas

Empty mastery before an attempt is expected. Reviewer navigation changes with selected
workspace; screenshots must show whose workspace is selected. Browser previews do not
prove forged-write protection; use the API isolation tests for that additional claim.
