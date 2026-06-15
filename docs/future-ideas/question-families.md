# Question Families

## Decision

Quizzy should eventually group near-duplicate questions into explicit question families so repeated
success on the same retrieval target does not look like broad independent mastery evidence.

Do not build this into the first confidence-weighted scheduler version. Until a real family model
exists, use each `question.id` as the family key.

## Definition

A question family is a set of separate `questions` rows that test the same learner retrieval target.
The wording, distractors, ordering, or presentation may differ, but success on one member should not
be counted as fully independent evidence from success on another member.

Examples:

- A generated rewording of the same prompt with the same correct answer.
- A multiple-choice variant with shuffled or lightly rewritten distractors.
- Two questions that ask for the same source fact from opposite phrasing.
- A repaired replacement for a flawed question that still tests the same retrieval target.

Non-examples:

- Questions linked to the same concept but testing meaningfully different constraints.
- A prerequisite question and an application question for the same concept.
- Questions that share a source paragraph but ask about different facts or relationships.

## Why It Matters

The mastery and scheduler model should distinguish repeated recognition from durable concept
coverage. If a learner answers five near-identical variants correctly, Quizzy should treat that as
strong evidence for that one retrieval target, not as equivalent to five independent healthy primary
questions.

Question families are useful for:

- capping repeated performance evidence;
- blocking recent-repeat spacing advances;
- preferring practice questions from families not used recently;
- keeping repaired/replacement questions connected to the original learning evidence;
- supporting future generation tools that intentionally create variants.

## Current V1 Behavior

V1 has no `question_family_id`. The family key is the question id:

```python
family_key = question.id
```

This means exact repeated attempts of the same question are capped, but generated near-duplicates are
not grouped yet.

Code and tests should still use the term "family key" where it keeps the future migration obvious,
but comments should make clear that the V1 family key is the exact question id.

## Future Schema

Add a nullable self-referential family key to `questions`:

```text
questions.question_family_id nullable
```

Possible interpretations:

- `NULL` means the question is its own family.
- A populated value points to a canonical root question for the family.

The runtime family key becomes:

```python
family_key = question.question_family_id or question.id
```

Indexes to consider:

- `(subject_id, question_family_id)`
- `(question_family_id)`

## Assignment Workflow

Question-family assignment should not be fully automatic at first. Near-duplicate detection is
useful, but the app should queue uncertain groupings for review rather than silently merging
unrelated questions.

Good initial assignment points:

- During question generation, when the generator intentionally creates variants.
- During question repair, when a replacement question supersedes an existing one.
- During stewardship review, when duplicate or near-duplicate questions are detected.

Potential review actions:

- mark as same family;
- split from family;
- choose canonical family root;
- move repaired question into original family;
- reject a suggested family match.

## Open Questions

- Should families be represented by a root `questions.id` or a separate `question_families` table?
- Should a family have its own title or source reference?
- Should family assignment be subject-scoped, source-document-scoped, or global?
- How should archived or suspect family roots behave?
- Should variants be generated intentionally for spaced retrieval, or only detected after the fact?

## Migration Path

1. Keep V1 code using `question.id` as the family key.
2. Add helper functions around family-key lookup before adding schema, so call sites stay stable.
3. Add `questions.question_family_id`.
4. Backfill existing rows as `NULL`; this preserves current behavior.
5. Update family-key lookup to `question.question_family_id or question.id`.
6. Add review tooling for suggested near-duplicate families.
7. Add practice-selection and mastery tests for two distinct questions in the same family.
