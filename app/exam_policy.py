from sqlalchemy.orm import Session

from app.models import Question, Quiz, QuizQuestion, QuizType


def require_exam_separation(session: Session, row) -> None:
    if isinstance(row, QuizQuestion):
        _require_matching_exam_flags(*_link_ends(session, row))


def _link_ends(session: Session, link: QuizQuestion) -> tuple[Quiz, Question]:
    quiz = link.quiz or session.get(Quiz, link.quiz_id)
    question = link.question or session.get(Question, link.question_id)
    if quiz is None or question is None:
        raise ValueError("Quiz question link refers to a quiz or question that cannot be read")
    return quiz, question


def _require_matching_exam_flags(quiz: Quiz, question: Question) -> None:
    if (quiz.quiz_type == QuizType.EXAM) != bool(question.exam_only):
        raise ValueError("Exam quizzes hold only exam questions, and other quizzes hold none")
