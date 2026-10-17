from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import Choice, Question, QuestionKeyPoint, Quiz, QuizQuestion, QuizStatus


def require_draft_content(session: Session, row) -> None:
    if isinstance(row, Quiz):
        _require_draft_quiz(session, row)
    elif isinstance(row, QuizQuestion):
        quiz = row.quiz or session.get(Quiz, row.quiz_id)
        _require_draft_quiz(session, quiz)
    elif isinstance(row, Choice | QuestionKeyPoint):
        _require_unpublished_answer_part(session, row)
    elif isinstance(row, Question) and _question_content_changed(session, row):
        _require_unpublished_question(session, row.id)


def _require_draft_quiz(session: Session, quiz: Quiz | None) -> None:
    if quiz is None or quiz in session.new:
        return
    previous_status = inspect(quiz).attrs.status.history.deleted
    statuses = [quiz.status, *previous_status]
    if any(status != QuizStatus.DRAFT for status in statuses):
        raise ValueError("MCP cannot change published quiz content; create a new draft")


def _question_content_changed(session: Session, question: Question) -> bool:
    if question in session.deleted:
        return True
    attributes = inspect(question).attrs
    content_columns = set(Question.__table__.columns.keys()) - {"health_state", "updated_at"}
    return any(attributes[name].history.has_changes() for name in content_columns)


def _require_unpublished_question(session: Session, question_id: int | None) -> None:
    if question_id is None:
        return
    published = session.scalar(
        select(Quiz.id)
        .join(QuizQuestion)
        .where(QuizQuestion.question_id == question_id, Quiz.status != QuizStatus.DRAFT)
        .limit(1)
    )
    if published is not None:
        raise ValueError("MCP cannot change a question used in a published quiz")


def _require_unpublished_answer_part(session: Session, part: Choice | QuestionKeyPoint) -> None:
    question_ids = list(inspect(part).attrs.question_id.history.deleted)
    question_ids.append(part.question_id)
    if part.question is not None:
        question_ids.append(part.question.id)
    for question_id in question_ids:
        _require_unpublished_question(session, question_id)
