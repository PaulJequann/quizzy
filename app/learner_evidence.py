from sqlalchemy import and_, or_

from app.models import Attempt, ExamSubmission


def learner_attempt_clause():
    return and_(
        Attempt.actor_role == "learner",
        Attempt.actor_id == Attempt.workspace_id,
        Attempt.submitted_at.is_not(None),
        or_(
            Attempt.exam_submission_id.is_(None),
            Attempt.exam_submission.has(ExamSubmission.released_at.is_not(None)),
        ),
    )


def is_learner_attempt(attempt: Attempt) -> bool:
    return (
        attempt.actor_role == "learner"
        and attempt.actor_id == attempt.workspace_id
        and attempt.submitted_at is not None
        and (
            attempt.exam_submission_id is None
            or (
                attempt.exam_submission is not None
                and attempt.exam_submission.released_at is not None
            )
        )
    )
