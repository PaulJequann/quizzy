# Confidence-Weighted Concept Scheduler

## Decision

Quizzy should evolve concept mastery from raw historical accuracy into a transparent concept-level
retrieval scheduler.

Use a hybrid model:

- correctness remains the objective answer signal;
- learner-reported retrieval friction adjusts evidence strength;
- concept-level review state decides when the concept is due again;
- coverage, question health, and primary-concept evidence gate mastery claims.
- learner question flags feed question health so bad questions do not poison mastery evidence.

This should stay understandable in the UI and debuggable in Python/Postgres. Do not start with an
opaque ML model.

## Product Goal

Quizzy should help a learner know which concepts are actually durable, not merely which concepts had
high recent quiz accuracy.

The current mastery model can tell that a learner answered `8/10` concept-linked questions
correctly. It cannot distinguish:

- fluent retrieval from a durable concept;
- a lucky correct guess;
- slow recognition of a repeated multiple-choice question;
- a wrong answer caused by one missed constraint;
- a wrong answer caused by a broken conceptual bridge.

The scheduler should add that missing signal with one low-friction post-answer input:

```text
How did retrieval feel?

[1] Shaky
[2] Effortful
[3] Fluent
```

This input is a retrieval-friction signal. It is not mastery by itself.

The same answer review surface should also support lightweight question issue reporting. Retrieval
feedback and question flags are separate signals:

```text
Retrieval feedback: "How did my recall feel?"
Question flag: "Is this question itself bad?"
```

Question flags should improve the question bank and question health. They should not directly erase
or rewrite learner performance evidence.

## Current State

The current implementation is centered in `app.services.grading_service`.

Current concept mastery:

- `submit_attempt` creates one `Attempt` and one `AttemptAnswer` per quiz question.
- `AttemptAnswer.is_correct` is the only learner-performance signal.
- `get_mastery_rows` computes mastery live from active concepts, active questions,
  `question_concepts`, and `attempt_answers`.
- Concept accuracy is `correct_attempts / total_attempts`.
- Concept status is:
  - `strong` when accuracy is at least `90`;
  - `improving` when accuracy is at least `70`;
  - `weak` otherwise.
- Concepts with zero attempts compute as weak, but `build_concept_queue_items` excludes them from
  the review queue.
- Parent concepts roll up attempt evidence from direct questions and descendant concepts.
- Primary and secondary concept roles are counted, but they do not change the score.
- `last_missed_at`, `missed_question_ids`, and `source_references` are retained as context.

Current practice assembly:

- `app.services.practice_service.assemble_mastery_review` gets weak/improving concept ids from
  `get_concept_queue_items`.
- Candidate questions must be active and not archived or suspect by question health.
- Mastery-review questions get a priority score when a priority concept is assigned as primary.
- Question health influences practice selection, but it does not currently change concept mastery.

Relevant current tables:

- `subjects`
- `concepts`
- `question_concepts`
- `questions`
- `choices`
- `quizzes`
- `quiz_questions`
- `attempts`
- `attempt_answers`
- `question_review_items`
- `concept_review_items`

There is no user table yet. Initial implementation can use a singleton learner profile, but schema
should be shaped so `user_id` can be added later without redesigning the algorithm.

## Target Terminology

- Retrieval friction
  - Learner self-report after answer reveal.
  - Values: `shaky`, `effortful`, `fluent`.
  - Stored on the answer event because it describes that retrieval moment.

- Spacing eligibility
  - Whether an answer is allowed to advance long-term concept review state.
  - Correct answers may be ineligible when they are same-session repeats, secondary-only evidence,
    suspect questions, or exact repeated question families.

- Concept review state
  - Durable scheduling state for a concept.
  - Includes interval, due date, last review date, lapse count, and spaced-correct streak.

- Performance evidence
  - Weighted correctness evidence derived from attempt answers.
  - Used to estimate current concept performance.

- Coverage evidence
  - Evidence that the learner has seen enough distinct healthy primary questions or question
    families to trust a mastery claim.

- Mastery status
  - A derived status from performance, retention, coverage, confidence, and unresolved misses.
  - Internal target states: `unseen`, `learning`, `review_due`, `stable`, `mastered`.
  - These states drive scheduling, sorting, and explanations. They are not the primary overview UI
    labels.

- Proficiency band
  - The glanceable learner-facing bucket used in overview UI.
  - Target bands remain simple: `weak`, `improving`, `strong`.
  - Proficiency answers "how am I doing?" while mastery state answers "what should Quizzy do next?"

- Question flag
  - Learner-submitted issue report about question quality.
  - Examples: incorrect answer, unclear wording, bad distractors, explanation issue, source mismatch,
    typo/formatting, or other.
  - Flags should create app-owned question review work through `question_review_items`.

## Product Behavior

After a learner answers a question and the app reveals the result, ask:

```text
How did retrieval feel?

1 Shaky
2 Effortful
3 Fluent
```

Button meanings:

- `Shaky`
  - The learner guessed, blanked, relied on surface cues, or cannot explain why the answer is right.
  - Correct + shaky is fragile positive evidence.
  - Wrong + shaky is a strong lapse signal.

- `Effortful`
  - The learner understands the concept but retrieval was slow, cognitively heavy, or missed a core
    constraint.
  - Correct + effortful is useful evidence, but the concept should remain in rotation.
  - Wrong + effortful is partial failure and should pull the concept forward.

- `Fluent`
  - The learner had immediate retrieval, no hint, no guessing, and can explain the answer.
  - Only correct fluent answers should grow the concept interval.
  - V1 should not allow `Fluent` on incorrect answers. Hide or disable the fluent control for wrong
    answers in the web UI, and reject `retrieval_friction_rating = 3` for an incorrect
    `AttemptAnswer` in web/API validation.
  - Later, wrong answers can collect a separate reason such as `conceptual_gap`,
    `missed_constraint`, `careless_slip`, or `ambiguous_question`.

The full practice loop must be keyboard-first. A learner should be able to answer questions and
record retrieval feel without reaching for the mouse.

Required keyboard behavior:

- number keys select answer choices during practice;
- the selected answer choice is visually obvious before submission;
- `Enter` submits the current question or advances to the next step when the state is valid;
- after answer reveal, number keys record retrieval feel:
  - `1` = Shaky
  - `2` = Effortful
  - `3` = Fluent
- retrieval-feel shortcuts must only be active after the answer result is visible;
- answer-choice shortcuts and retrieval-feel shortcuts must not conflict in the same UI state;
- buttons must still be real accessible buttons for mouse, touch, and screen-reader users.

The preferred UX is one post-answer hotkey/button row, not a survey.

Add a separate lightweight question-quality action on the same review surface:

```text
Report issue
```

Do not place issue categories in the main retrieval-feel hotkey row. Reporting a bad question should
be optional and visually secondary so it does not slow down normal practice.

Initial issue categories:

- incorrect answer;
- unclear wording;
- bad distractors;
- explanation issue;
- source mismatch;
- typo or formatting;
- other.

Question flags should create or refresh question review work. They should not directly modify the
learner's retrieval rating and should not automatically remove a wrong answer from mastery evidence.

## Core Design Rules

1. Correctness remains the ground-truth answer signal.
2. Retrieval friction modifies evidence strength and review scheduling.
3. Concept schedules advance only on spacing-eligible evidence.
4. Primary concept evidence can establish mastery.
5. Secondary concept evidence can inform mastery but cannot establish mastery alone.
6. Suspect questions should not advance mastery or spacing.
7. Repeating the same exact question should not push a concept far into the future.
8. Parent concept mastery should not hide weak or unseen child concepts.
9. Mastery status must be explainable with reason codes.
10. The first version should be deterministic and transparent.
11. Overview UI must stay proficiency-first and glanceable; internal scheduler states should not
    become primary row badges.
12. Retrieval feedback and question-quality flags must remain distinct signals.
13. Bad-question flags should affect mastery through question health weights, not through direct
    manual edits to learner correctness evidence.

## Target Schema

### `attempt_answers`

Add nullable fields:

- `retrieval_friction_rating integer nullable`
  - Allowed values:
    - `1` = shaky
    - `2` = effortful
    - `3` = fluent
  - Nullable for old answers and for flows that do not collect the signal.

- `response_time_ms integer nullable`
  - Optional in the first migration if the UI can measure it cheaply.
  - Useful later for detecting fluency mismatches.

- `hint_used boolean not null default false`
  - Optional in the first migration.
  - Keep default false until hints exist.

Add a check constraint:

```sql
retrieval_friction_rating IS NULL OR retrieval_friction_rating IN (1, 2, 3)
```

### `question_review_items`

Reuse the existing table for learner-reported question issues. Do not create a separate flag table in
v1 unless product needs require flag history separate from review work.

Recommended v1 mapping:

- `reason`
  - Human-readable report summary, including category and optional note.
- `suggested_action`
  - Category-specific action such as `verify_answer`, `revise_stem`, `improve_distractors`,
    `fix_explanation`, `verify_source`, or `fix_formatting`.
- `evidence_json`
  - Include structured flag details:

```json
{
  "source": "learner_flag",
  "category": "bad_distractors",
  "note": "Two choices are obviously unrelated.",
  "attempt_answer_id": 123
}
```

If preserving every learner flag becomes important, add a future `question_flags` event table and
let `question_review_items` remain the deduplicated open work queue.

### `concept_review_states`

Create this table.

Initial singleton-learner version:

- `id primary key`
- `concept_id FK -> concepts.id on delete cascade`
- `interval_days integer not null default 1`
- `ease_factor float not null default 2.0`
- `due_at timestamptz nullable`
- `last_review_at timestamptz nullable`
- `successful_spaced_reviews integer not null default 0`
- `consecutive_spaced_correct integer not null default 0`
- `lapse_count integer not null default 0`
- `last_friction_rating integer nullable`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Constraints/indexes:

- unique index on `concept_id`
- check `interval_days >= 0`
- check `ease_factor >= 1.3`
- check `last_friction_rating IS NULL OR last_friction_rating IN (1, 2, 3)`

Future multi-user version should add `user_id` and change uniqueness to `(user_id, concept_id)`.
Do not bake singleton assumptions into service names or dataclasses.

### Future `question_family_id`

Do not block v1 on question families. For v1, treat each `question.id` as its own family.

Later add:

- `questions.question_family_id nullable`

This will cap repeated evidence from near-duplicate variants.

Even before `question_family_id` exists, the implementation must consistently use a v1 family key:

```python
family_key = question.question_family_id or question.id
```

Until `question_family_id` exists, this is effectively:

```python
family_key = question.id
```

Use that family key for:

- performance evidence caps;
- recent-repeat spacing eligibility checks;
- practice selection reasons;
- future migration to real question families.

## Service Design

Add a new service module:

```text
app/services/concept_scheduler_service.py
```

Responsibilities:

- validate retrieval friction input;
- decide whether an answer is spacing-eligible for each linked concept;
- update concept review state after an attempt answer receives friction;
- provide due concept ids for mastery review;
- provide reason codes for UI/debugging.

Add or extend a question issue reporting function in `app.services.question_health_service`:

```python
def report_question_issue(
    db: Session,
    *,
    question_id: int,
    category: str,
    note: str = "",
    attempt_answer_id: int | None = None,
) -> dict[str, Any]:
    ...
```

Responsibilities:

- validate category;
- create or refresh an open `QuestionReviewItem`;
- store structured flag evidence in `evidence_json`;
- optionally update `Question.health_state` to `NEEDS_REVIEW`;
- return the review item dict for API/UI use.

Suggested category mapping:

```python
QUESTION_FLAG_ACTIONS = {
    "incorrect_answer": "verify_answer",
    "unclear_wording": "revise_stem",
    "bad_distractors": "improve_distractors",
    "explanation_issue": "fix_explanation",
    "source_mismatch": "verify_source",
    "typo_formatting": "fix_formatting",
    "other": "review_question",
}
```

Suggested severity mapping:

```python
HIGH_SEVERITY_FLAGS = {"incorrect_answer", "source_mismatch"}
MEDIUM_SEVERITY_FLAGS = {
    "unclear_wording",
    "bad_distractors",
    "explanation_issue",
}
LOW_SEVERITY_FLAGS = {"typo_formatting", "other"}
```

High and medium severity flags should set `Question.health_state = NEEDS_REVIEW` unless the question
is already `SUSPECT` or `ARCHIVED`. Low severity flags can create review work without changing health
state in v1.

Suggested public functions:

```python
def record_retrieval_feedback(
    db: Session,
    *,
    attempt_answer_id: int,
    friction_rating: int,
    response_time_ms: int | None = None,
) -> list[ConceptScheduleUpdate]:
    ...

def update_concept_schedule_from_answer(
    db: Session,
    *,
    answer: AttemptAnswer,
    concept: Concept,
    role: ConceptAssignmentRole,
    friction_rating: int,
    now: datetime,
) -> ConceptScheduleUpdate:
    ...

def due_concept_ids(
    db: Session,
    *,
    now: datetime | None = None,
    subject_id: int | None = None,
) -> list[int]:
    ...

def spacing_eligible(
    *,
    answer: AttemptAnswer,
    concept: Concept,
    role: ConceptAssignmentRole,
    state: ConceptReviewState,
    now: datetime,
) -> tuple[bool, list[str]]:
    ...
```

Use a dataclass for update explanations:

```python
@dataclass(frozen=True)
class ConceptScheduleUpdate:
    concept_id: int
    previous_due_at: datetime | None
    due_at: datetime | None
    previous_interval_days: int
    interval_days: int
    spacing_eligible: bool
    reason_codes: list[str]
```

## Scheduling Algorithm

Use simple interval bands for v1. They are easier to debug than full SM-2 math.

```python
INTERVAL_BANDS = [0, 1, 3, 7, 14, 30, 60, 120]
MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.0
```

Map friction:

```python
SHAKY = 1
EFFORTFUL = 2
FLUENT = 3
```

Schedule update rules:

```python
def update_interval(state, *, is_correct, friction, spacing_eligible, now):
    current = state.interval_days

    if not is_correct:
        state.lapse_count += 1
        state.consecutive_spaced_correct = 0
        state.last_review_at = now
        state.last_friction_rating = friction

        if friction == SHAKY:
            state.interval_days = 1
            state.ease_factor = max(MIN_EASE_FACTOR, state.ease_factor - 0.25)
        else:
            state.interval_days = max(1, previous_band(current))
            state.ease_factor = max(MIN_EASE_FACTOR, state.ease_factor - 0.15)

        state.due_at = now + days(state.interval_days)
        return

    if friction == SHAKY:
        state.interval_days = min(current or 1, 2)
        state.ease_factor = max(MIN_EASE_FACTOR, state.ease_factor - 0.10)
        state.due_at = now + days(state.interval_days)
        state.last_review_at = now
        state.last_friction_rating = friction
        return

    if not spacing_eligible:
        state.last_friction_rating = friction
        return

    if friction == EFFORTFUL:
        state.interval_days = max(1, current)
    elif friction == FLUENT:
        state.interval_days = next_band(current)
        state.ease_factor = min(2.8, state.ease_factor + 0.05)

    state.successful_spaced_reviews += 1
    state.consecutive_spaced_correct += 1
    state.due_at = now + days(state.interval_days)
    state.last_review_at = now
    state.last_friction_rating = friction
```

Spacing eligibility gates:

- answer question is active;
- question health is not `SUSPECT` or `ARCHIVED`;
- concept is active;
- assignment role is primary;
- answer is correct for interval growth;
- no hint was used;
- last review was not too recent.
- the same question family was not already used for this concept inside the repeat window.

Initial same-session guard:

```python
MIN_SPACING_HOURS = 12
spacing_eligible = (
    state.last_review_at is None
    or now - state.last_review_at >= timedelta(hours=MIN_SPACING_HOURS)
)
```

Initial repeat-family guard:

```python
MIN_FAMILY_REPEAT_HOURS = 12

spacing_eligible = spacing_eligible and not same_family_seen_recently(
    concept_id=concept.id,
    family_key=question.id,  # v1 family key
    since=now - timedelta(hours=MIN_FAMILY_REPEAT_HOURS),
)
```

Even when an answer is not spacing-eligible, it can still contribute to performance evidence.

## Mastery Read Model Changes

Keep live computation as the first implementation path. Do not add a persisted mastery cache until
performance requires it.

Extend `ConceptMastery` in `app.services.grading_service` with scheduling fields:

- `due_at: datetime | None`
- `interval_days: int | None`
- `consecutive_spaced_correct: int`
- `lapse_count: int`
- `retrieval_friction_counts: dict[int, int]` or explicit counts
- `reason_codes: list[str]`

Add an internal mastery-state vocabulary:

- `unseen`
- `learning`
- `review_due`
- `stable`
- `mastered`

Keep a separate learner-facing proficiency band for overview UI:

- `weak`
- `improving`
- `strong`

The overview and subject pages should continue to group concepts by proficiency band. Internal
states should affect sorting, queue selection, and small supporting metadata, not replace the main
badge.

Suggested default mapping:

- `unseen` -> proficiency band `weak`, displayed as secondary copy `New` or `Not started` when useful;
- `learning` -> proficiency band `weak`;
- `review_due` -> keep the current proficiency band from performance evidence, with secondary copy
  `Due now` or `Review due`;
- `stable` -> proficiency band `improving`;
- `mastered` -> proficiency band `strong`.

Do not display raw internal state strings such as `review_due` in templates. Use presentation labels
and secondary metadata.

During migration, the UI may temporarily map:

- `unseen` -> weak visual treatment with clearer copy;
- `learning` -> weak;
- `review_due` -> improving or dedicated due treatment;
- `stable` -> improving;
- `mastered` -> strong.

### Performance Evidence Formula

For v1, keep raw accuracy available but add a conservative weighted estimate for status decisions.

Use beta-style evidence:

```python
ALPHA0 = 1.0
BETA0 = 1.0

SUCCESS_FRICTION_WEIGHT = {
    1: 0.45,
    2: 0.85,
    3: 1.15,
    None: 1.0,
}

FAILURE_FRICTION_WEIGHT = {
    1: 1.25,
    2: 0.90,
    3: 0.80,
    None: 1.0,
}

ROLE_WEIGHT = {
    "primary": 1.0,
    "secondary": 0.30,
}

HEALTH_WEIGHT = {
    "healthy": 1.0,
    "untested": 0.8,
    "needs_review": 0.4,
    "suspect": 0.0,
    "archived": 0.0,
}
```

Learner flags influence mastery through `Question.health_state`. A high-severity flag should reduce
future mastery impact by moving the question to `NEEDS_REVIEW`, and existing health weighting then
downweights it. Do not retroactively delete the learner's answer or automatically convert a miss into
neutral evidence.

For each answer linked to a concept:

```python
base_weight = ROLE_WEIGHT[role] * HEALTH_WEIGHT[question.health_state]

if answer.is_correct:
    weighted_success += base_weight * SUCCESS_FRICTION_WEIGHT[answer.retrieval_friction_rating]
else:
    weighted_failure += base_weight * FAILURE_FRICTION_WEIGHT[answer.retrieval_friction_rating]
```

Cap repeated evidence from the same question family so exact repeats cannot certify mastery:

```python
MAX_EFFECTIVE_WEIGHT_PER_FAMILY = 1.5

family_key = question.id  # v1; later question.question_family_id or question.id

family_success_weight[family_key] += success_weight_for_answer
family_failure_weight[family_key] += failure_weight_for_answer
```

Use `effective_success` and `effective_failure` in the beta estimate. If both success and failure
exist for the same family, cap the total family evidence rather than each side independently:

```python
effective_success = 0.0
effective_failure = 0.0

for family_key in family_keys:
    success = family_success_weight[family_key]
    failure = family_failure_weight[family_key]
    total = success + failure
    scale = min(1.0, MAX_EFFECTIVE_WEIGHT_PER_FAMILY / total) if total else 1.0
    effective_success += success * scale
    effective_failure += failure * scale
```

Then:

```python
alpha = ALPHA0 + effective_success
beta = BETA0 + effective_failure
posterior_mean = alpha / (alpha + beta)
variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1))
conservative_performance = max(0.0, posterior_mean - sqrt(variance))
```

Use `posterior_mean` or raw accuracy for display. Use `conservative_performance` for mastery
status.

### Coverage Score

V1 can use unique primary questions seen as a proxy for unique question families.

```python
target_unique_primary_questions = min(
    healthy_primary_question_count,
    max(3, ceil(0.35 * healthy_primary_question_count)),
)

coverage_score = min(
    1.0,
    unique_primary_questions_seen / target_unique_primary_questions,
)
```

If `healthy_primary_question_count` is zero, set target to `3` and emit reason code
`too_few_healthy_questions`.

### Confidence Score

```python
evidence_confidence = 1.0 - exp(-effective_attempt_count / 6.0)
confidence_score = min(evidence_confidence, coverage_score)
```

### Retention Score

Use `due_at` for status. A separate retrievability score can be added for sorting:

```python
TARGET_RETENTION = 0.90

def retrievability(now, last_review_at, interval_days):
    if not last_review_at or not interval_days:
        return 0.0
    elapsed_days = max(0.0, (now - last_review_at).total_seconds() / 86400)
    return TARGET_RETENTION ** (elapsed_days / interval_days)
```

### Status Classification

Define `unresolved_primary_miss_count` before using it. V1 should derive unresolved misses from the
latest answer per primary question for the concept:

```python
latest_primary_answer_by_question = latest AttemptAnswer for each active question
where QuestionConcept.role == PRIMARY
and QuestionConcept.concept_id == concept.id

unresolved_primary_miss_count = count(
    answer for answer in latest_primary_answer_by_question.values()
    if answer is not None and not answer.is_correct
)
```

Historical misses can still be displayed as context, but they should not block mastery when the
latest primary evidence for that question is correct.

Initial rule set:

```python
if primary_attempts == 0 and secondary_attempts == 0:
    status = "unseen"
elif primary_attempts == 0 and secondary_attempts > 0:
    status = "learning"  # only_secondary_evidence
elif unresolved_primary_miss_count > 0:
    status = "learning"
elif conservative_performance < 0.70:
    status = "learning"
elif due_at is not None and now >= due_at:
    status = "review_due"
elif (
    conservative_performance >= 0.84
    and coverage_score >= 0.80
    and confidence_score >= 0.70
    and consecutive_spaced_correct >= 3
    and interval_days >= 30
):
    status = "mastered"
elif conservative_performance >= 0.75 and coverage_score >= 0.50:
    status = "stable"
else:
    status = "learning"
```

After computing internal `status`, derive a separate proficiency band:

```python
def proficiency_band(stats):
    if stats.conservative_performance >= 0.84 and stats.coverage_score >= 0.70:
        return "strong"
    if stats.conservative_performance >= 0.70:
        return "improving"
    return "weak"
```

Then apply small guardrails:

```python
if stats.status == "unseen":
    band = "weak"
if stats.status == "learning" and "only_secondary_evidence" in stats.reason_codes:
    band = "weak"
```

`review_due` should not automatically become its own proficiency band. A strong concept can be due
for maintenance and still display as `Strong` in overview lists.

Reason codes:

- `unseen`
- `only_secondary_evidence`
- `low_performance`
- `low_confidence`
- `low_coverage`
- `review_due`
- `unresolved_primary_miss`
- `too_few_healthy_questions`
- `suspect_question_evidence_ignored`
- `needs_fluent_spaced_retrieval`
- `same_session_repeat_not_advanced`

## Parent Concept Rollups

Do not declare a parent concept mastered only because descendant attempts sum to a high accuracy.

Expose two different parent readings:

- direct parent mastery
  - evidence from questions assigned directly to the parent;
  - useful when the parent itself is a broad learning objective.

- child bucket progress
  - aggregate child statuses and scores;
  - useful when the parent is a study bucket.

For v1, keep existing rolled-up counts for continuity but add child-status distribution:

```text
3 mastered
2 stable
1 review due
4 learning
1 unseen
```

A parent bucket should be considered mastered only when:

- direct parent mastery is mastered or not applicable;
- required child concepts are stable or mastered;
- no required child concept is unseen, learning, or review due.

If there is no required/optional marker yet, treat all active children as required.

## Practice Assembly Changes

Update `app.services.practice_service.assemble_mastery_review` so review selection uses both:

- weak/learning concepts from performance evidence;
- due concepts from `concept_review_states`.

Priority order:

1. concepts with unresolved primary misses;
2. due concepts;
3. learning concepts with low conservative performance;
4. unseen concepts for coverage exploration;
5. stable/mastered concepts due for maintenance.

Question selection rules:

- Prefer active healthy primary questions for the target concept.
- Prefer question families not used recently.
- Exclude suspect and archived questions.
- Downweight `needs_review` questions.
- Include a small exploration quota for untested but active questions.
- Do not use secondary-only questions to advance concept schedule.

Store selection reasons in `quiz_questions.selection_reason`, such as:

```text
mastery_review: concept due for spaced retrieval
mastery_review: low confidence from shaky correct answers
mastery_review: learning concept with unresolved primary miss
mastery_review: coverage slot for unseen concept
```

## UI Presentation Model

The mastery UI should stay glanceable.

Primary overview buckets:

```text
Weak
Improving
Strong
```

These are proficiency bands, not internal scheduler states. They should remain the main summary
counts and the main row badges in views like the subject mastery screen.

Internal states should appear only as secondary context when they change what the learner should do
next:

```text
Due now
New
Needs coverage
Only seen indirectly
Last answer shaky
Next review Jun 22
```

Examples:

```text
Route 53 Health Aware Routing
0% mastery    Weak    Due now
```

```text
IAM policy evaluation
91% mastery   Strong   Next review Jul 12
```

```text
S3 lifecycle rules
Not started   Weak     3 questions ready
```

For overview cards, keep the current scan pattern:

```text
33% Overall Mastery
Weak 3
Improving 0
Strong 1
```

Optionally add one compact review cue if it is useful:

```text
2 due
```

Do not add `Unseen`, `Review due`, `Stable`, and `Mastered` as additional top-level overview
categories. They create too much vocabulary for the at-a-glance view. Use them internally for
sorting, scheduling, and detail explanations.

Subject detail grouping should remain proficiency-oriented:

- Weak concepts to review
- Improving concepts
- Strong concepts

Within each group, sort by internal urgency:

1. unresolved primary misses;
2. due now;
3. low coverage;
4. shaky recent evidence;
5. lowest conservative performance;
6. concept name.

This keeps the UI simple while making the smarter engine visible through ordering and compact
secondary cues.

## Web Flow

Current submit flow:

- `POST /quizzes/{quiz_id}/submit`
- `submit_attempt`
- `question_health_service.record_attempt_evidence`
- redirect to `/attempts/{attempt_id}/results`

V1 implementation options:

### Option A: Collect feedback on results page

This is easiest and least invasive.

1. Keep attempt submission unchanged.
2. On `/attempts/{attempt_id}/results`, show feedback buttons for each answer.
3. Each button posts to a new route:

```text
POST /attempt-answers/{answer_id}/retrieval-feedback
```

Form fields:

- `retrieval_friction_rating`
- `response_time_ms` optional

The route:

- validates the answer exists;
- rejects `retrieval_friction_rating = 3` when `answer.is_correct` is false;
- stores feedback on `attempt_answers`;
- calls `concept_scheduler_service.record_retrieval_feedback`;
- redirects back to the attempt results page.

Add a separate issue-reporting route:

```text
POST /questions/{question_id}/report-issue
```

Form fields:

- `category`
- `note` optional
- `attempt_answer_id` optional

The route:

- validates the question exists;
- validates the category;
- calls `question_health_service.report_question_issue`;
- redirects back to the attempt results page or question detail page.

### Option B: Collect feedback during answer reveal

Use this later when the quiz UI reveals answers one by one. It is the better product experience but
requires more frontend work.

## Keyboard-First Practice Loop

Keyboard shortcuts are part of the feature, not a polish task.

The target interaction model is:

1. The learner opens a practice question.
2. Pressing `1`, `2`, `3`, or `4` selects the corresponding answer choice.
3. Pressing `Enter` submits the selected answer.
4. The UI reveals correctness, explanation, and concept context.
5. Pressing `1`, `2`, or `3` records retrieval feel.
6. Pressing `Enter` advances to the next question when retrieval feel has been recorded, or when
   feedback is optional and the learner intentionally skips it.

Implementation notes:

- The shortcut dispatcher should be scoped to the active practice/results surface.
- Ignore shortcuts while focus is inside text inputs, textareas, selects, dialogs, or editable
  elements.
- Use explicit UI state to decide what `1` means:
  - before reveal, `1` selects answer choice one;
  - after reveal, `1` records `Shaky`;
  - never let both handlers run for one keypress.
- Use `event.key` for number keys and avoid relying on keyboard layout-specific codes.
- Do not hijack browser/system shortcuts.
- Keep click handlers and keyboard handlers on the same underlying actions so behavior cannot drift.
- Set focus intentionally after reveal so screen-reader and keyboard users land near the
  retrieval-feel controls.
- Expose shortcut hints in button labels or compact helper text, for example `1 Shaky`,
  `2 Effortful`, `3 Fluent`.
- Add tests for keyboard behavior where feasible with browser-level tests; route/service tests are
  not enough for this requirement.

The first keyboard implementation can support the current full-submit quiz page, but the long-term
target is a one-question-at-a-time practice surface where answer reveal and retrieval feedback happen
inside the same flow.

Question issue reporting should be reachable by keyboard, but it should not consume the primary
number-key answer or retrieval-feel shortcuts. Use an ordinary focused button or a non-conflicting
shortcut only if the UI later has a documented command pattern.

### Current Full-Submit Quiz Page

The current `quizzes/take.html` flow can render multiple questions in one form and submit them all at
once. Until a one-question-at-a-time practice surface exists, define the active question like this:

1. A question card becomes active when focus enters one of its answer controls.
2. If no question card contains focus, the active question is the first unanswered visible question.
3. Pressing `1`, `2`, `3`, or `4` selects the corresponding answer for the active question.
4. After a selection, active question moves to the next unanswered visible question.
5. Pressing `Enter` submits the full quiz only when every required visible question has an answer.
6. If the quiz allows unanswered questions, `Enter` may submit with unanswered questions only after
   the same validation/confirmation behavior used by mouse submission.

This keeps keyboard behavior deterministic in the existing form while leaving room for the better
one-question-at-a-time reveal flow.

## API/MCP Surface

Add API route:

```text
POST /api/attempt-answers/{answer_id}/retrieval-feedback
```

Request body:

```json
{
  "retrieval_friction_rating": 2,
  "response_time_ms": 4200
}
```

Response:

```json
{
  "attempt_answer_id": 123,
  "updates": [
    {
      "concept_id": 10,
      "interval_days": 7,
      "due_at": "2026-06-22T15:00:00Z",
      "spacing_eligible": true,
      "reason_codes": ["effortful_correct_interval_held"]
    }
  ]
}
```

MCP can use this later, but it does not need to be part of v1 unless agents are submitting attempts.

Add API route:

```text
POST /api/questions/{question_id}/report-issue
```

Request body:

```json
{
  "category": "bad_distractors",
  "note": "Two distractors are obviously unrelated.",
  "attempt_answer_id": 123
}
```

Response:

```json
{
  "review_item": {
    "question_id": 10,
    "reason": "Learner flagged bad distractors.",
    "suggested_action": "improve_distractors",
    "status": "open"
  },
  "health_state": "needs_review"
}
```

MCP question repair tools can later use these app-created review items as repair work.

## Implementation Plan

### Phase 1: Store retrieval friction

1. Add nullable columns to `attempt_answers`:
   - `retrieval_friction_rating`
   - `response_time_ms`
   - optionally `hint_used`
2. Add SQLAlchemy model fields.
3. Add Pydantic schemas for retrieval feedback.
4. Add web and API routes to record feedback.
5. Add results-page buttons for `Shaky`, `Effortful`, and `Fluent`.
6. Add keyboard shortcuts for retrieval feedback:
   - `1` posts `Shaky`;
   - `2` posts `Effortful`;
   - `3` posts `Fluent` only for correct answers;
   - shortcuts are disabled while typing in form controls.
7. Test:
   - valid ratings persist;
   - invalid ratings are rejected;
   - fluent feedback is rejected for incorrect answers;
   - old attempts without ratings still render;
   - feedback can be submitted from the results page;
   - retrieval feedback can be submitted with keyboard shortcuts.

### Phase 1.6: Add question issue reporting

Implement learner question flags through existing question health/review infrastructure.

1. Add Pydantic schema for question issue reports.
2. Add `question_health_service.report_question_issue`.
3. Add web route `POST /questions/{question_id}/report-issue`.
4. Add API route `POST /api/questions/{question_id}/report-issue`.
5. Add a secondary `Report issue` action to attempt results.
6. Use categories:
   - `incorrect_answer`
   - `unclear_wording`
   - `bad_distractors`
   - `explanation_issue`
   - `source_mismatch`
   - `typo_formatting`
   - `other`
7. Create or refresh an open `QuestionReviewItem`.
8. Set `Question.health_state = NEEDS_REVIEW` for high/medium severity categories.
9. Test:
   - issue report creates a question review item;
   - duplicate open issue of the same category refreshes existing review work instead of spamming;
   - severe categories mark the question as `NEEDS_REVIEW`;
   - typo/formatting creates review work without necessarily changing health state;
   - issue report does not mutate `AttemptAnswer.is_correct`;
   - mastery scoring downweights flagged questions through question health.

### Phase 1.5: Keyboard-first answer flow

Implement or verify keyboard shortcuts for answering practice questions.

1. On the practice/take page, map number keys to answer choices for the active question.
2. Show the selected answer clearly.
3. Let `Enter` submit or advance only when the current state is valid.
4. Prevent shortcut handling while focus is in text entry, selects, dialogs, or editable elements.
5. Ensure answer shortcuts are inactive once retrieval-feel shortcuts are active.
6. For the current full-submit page, implement the active-question behavior described above.
7. Test:
   - number keys select the expected answer choice;
   - `Enter` submits the selected answer;
   - the active question advances after a keyboard selection on the full-submit page;
   - shortcut handling ignores form-control focus;
   - answer shortcuts do not conflict with retrieval-feel shortcuts.

### Phase 2: Add concept review state

1. Create `concept_review_states`.
2. Add SQLAlchemy model and relationship if useful.
3. Add `concept_scheduler_service`.
4. On feedback submission, update review state for active concepts linked to the answered question.
5. Only primary active concept links can advance spacing.
6. Secondary links can record reason codes but should not advance interval.
7. Test:
   - wrong shaky resets interval to one day;
   - correct shaky keeps the concept due soon;
   - correct effortful holds the interval;
   - correct fluent advances to the next band when spacing-eligible;
   - correct fluent does not advance when same-session ineligible;
   - correct fluent does not advance when the same question family was seen inside the repeat window;
   - suspect questions do not advance interval.

### Phase 3: Extend mastery rows

1. Extend `ConceptMastery` with review state fields.
2. Join or lookup `concept_review_states` in `get_mastery_rows`.
3. Add retrieval friction counts and reason codes.
4. Introduce internal status computation behind a helper function.
5. Add a separate proficiency-band helper for overview UI.
6. Keep old raw accuracy available for display and backward compatibility.
7. Test:
   - no attempts returns `unseen`;
   - only secondary attempts returns `learning` with `only_secondary_evidence`;
   - historical misses do not block mastery after the latest primary answer is correct;
   - latest incorrect primary answers produce `unresolved_primary_miss`;
   - repeated exact-question attempts are capped by the v1 family key;
   - due concept returns `review_due`;
   - due strong concepts still display in the `strong` proficiency band;
   - sufficient spaced fluent evidence can become `mastered`;
   - low coverage blocks `mastered`.

### Phase 4: Update mastery review selection

1. Update `build_concept_queue_items` or add a new queue builder that understands new statuses.
2. Include due concepts even when raw accuracy is high.
3. Include unseen concepts as coverage slots when building broad mastery review.
4. Update `assemble_mastery_review` to prioritize due/learning concepts.
5. Preserve deterministic ordering.
6. Test:
   - due stable concept appears in mastery review;
   - mastered but not due concept does not dominate review;
   - unseen concepts enter through coverage quota;
   - flagged `NEEDS_REVIEW` questions are downweighted in mastery-sensitive review;
   - review quiz uses healthy primary questions when available.

### Phase 5: Improve hierarchy display

1. Keep existing concept tree layout.
2. Keep parent/child grouping proficiency-oriented.
3. Add parent child-status distribution only in detail or expanded context, not as top-level
   overview categories.
4. Add compact secondary UI copy from reason codes:
   - "Due for spaced retrieval"
   - "Only seen indirectly"
   - "Needs more primary-question coverage"
   - "Correct recently, but marked shaky"
5. Avoid showing parent rollup as a simple mastered state when children are weak or unseen.
6. Test hierarchy rollups with mixed child statuses.

## Testing Requirements

Use Testcontainers Postgres for DB/API/integration tests. Do not introduce SQLite test paths.

Minimum tests:

- model migration test includes new columns/table;
- retrieval feedback API persists rating;
- web results page exposes feedback controls;
- web/API reject fluent retrieval feedback for incorrect answers;
- keyboard shortcuts select answers during practice;
- keyboard shortcuts record retrieval feedback after answer reveal/results;
- shortcut handlers ignore text inputs and editable controls;
- question issue reporting creates or refreshes `QuestionReviewItem`;
- severe question flags update question health to `NEEDS_REVIEW`;
- question flags do not mutate stored correctness evidence;
- repeated exact-question evidence is capped using `question.id` as the v1 family key;
- latest-answer unresolved miss logic works for primary concept evidence;
- scheduler interval transitions;
- spacing eligibility gates;
- mastery status classification;
- proficiency-band mapping stays separate from internal mastery state;
- mastery queue includes due concepts;
- parent rollup does not hide child weakness.

Run normal checks after code changes:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
make test
```

Run CodeScene on every touched source file before commit, per `AGENTS.md`.

## Migration Notes

Use Alembic.

Do not use `Base.metadata.create_all()` for integration test schema setup.

Existing `attempt_answers` rows should be valid with `retrieval_friction_rating = NULL`.

When creating `concept_review_states`, do not backfill one row per concept unless the first UI needs
it. The scheduler can lazily create state on first feedback or first due-state lookup.

## Non-Goals For V1

- Full FSRS implementation.
- Bayesian knowledge tracing.
- IRT/Elo-style question or concept ratings.
- Per-user accounts.
- Question-family clustering.
- Response-time-based automatic friction correction.
- Wrong-answer reason taxonomy.
- Background jobs.
- Persisted mastery cache.

These can be added after the transparent concept scheduler proves useful.

## Open Questions

- Should the results page require retrieval feedback before showing final completion, or should it
  be optional?
- Should a correct shaky answer update `last_review_at`, or should only spacing-eligible answers do
  so?
- How much unseen-concept exploration should `mastery_review` include by default?
- Should subject mastery count parent buckets, leaf concepts, or both?
- When users exist, should concept review state be per user, per profile, or per study workspace?

## Recommended First PR

The first implementation PR should be intentionally small:

1. Add `retrieval_friction_rating` to `attempt_answers`.
2. Add a web route and API route to record feedback.
3. Add feedback buttons and `1`/`2`/`3` shortcuts to attempt results.
4. Add or verify answer-choice keyboard shortcuts in the practice flow.
5. Add tests for persistence, validation, and keyboard behavior.

Do not change mastery scoring in the same PR. Once the signal is being collected, add concept review
state and scheduling in the next PR.
