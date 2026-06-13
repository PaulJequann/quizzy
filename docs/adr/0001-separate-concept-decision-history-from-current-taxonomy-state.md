# Separate Concept Decision History From Current Taxonomy State

Quizzy records concept stewardship decisions in an append-only concept decision ledger, but current
app behavior reads from current taxonomy state such as concepts, hierarchy placement, aliases, and
review state. This keeps concept resolution and mastery behavior straightforward while preserving
the history and rationale for merges, renames, aliases, archives, reassignments, and approvals.
