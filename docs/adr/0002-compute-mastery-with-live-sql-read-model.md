# Compute Mastery With a Live SQL Read Model

Quizzy computes concept mastery from current concept state, question assignments, and attempt
answers through a live SQL-backed read model instead of storing persisted mastery summaries. This
keeps mastery correct while concept hierarchy, assignment, merge, split, archive, and review
decisions remain highly mutable; persisted summaries can be introduced later if measured read
performance requires caching.
