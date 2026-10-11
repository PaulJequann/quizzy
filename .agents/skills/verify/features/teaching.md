# Prepare next practice

The reviewer copies a teaching brief grounded in the selected learner's study evidence.

## Sub-features

- teaching.evidence includes wrong answers, correct answers, source references, and due state.
- teaching.copy copies the brief or selects it when clipboard access is unavailable.

## How to get to it (user POV)

Select the learner on **Switch workspace**, then open **Prepare next practice** at `/teaching`.
Agents can also request `prepare_next_practice` with the learner's explicit workspace ID.

## Driving it with T3 preview

Preconditions: study.md completed, reviewer signed in with learner workspace selected.

- Click `role=link[name='Prepare next practice']`. Read `#teaching-brief` through the
  rendered textarea value. Require the learner workspace, **Backups**, **Tests**,
  `testing/pytest.md#basics`, due state, and an explicit thin-evidence qualification.
- Click `role=button[name='Copy brief']`; require **Brief copied.** or
  **Brief selected. Use Copy on your device to copy it.**.
- For the fallback, call `preview_evaluate` with
  `Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})`
  for this tab only, then click again. Read
  `({start: document.querySelector('#teaching-brief').selectionStart, end: document.querySelector('#teaching-brief').selectionEnd, length: document.querySelector('#teaching-brief').value.length})`.
  Require start zero, end equal to length, and the fallback status.
- Select **Own study** and open `/teaching`. Require no learner mistake in that brief.

## Gotchas

No model runs inside Quizzy. The copied brief is the output, not a generated quiz.
Clipboard permissions differ by browser and HTTP origin. Do not claim clipboard content
was verified from the status message alone; verify it only if the driver can read clipboard.
