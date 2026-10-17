from app.models import Attempt, AttemptAnswer, ExamAssignment, ExamSubmission
from app.services.exam_results import (
    AnswerFeedback,
    ChoiceFeedback,
    ExamRecord,
    ReviewItem,
    exam_result,
    review_items,
)

EXAM_SESSION_TYPE = "exam"


def exam_attempt(
    record: ExamRecord, assignment: ExamAssignment, submission: ExamSubmission
) -> Attempt:
    result = exam_result(record)
    return Attempt(
        quiz_id=assignment.source_quiz_id,
        exam_submission_id=submission.id,
        session_type=EXAM_SESSION_TYPE,
        score=result.score,
        total_questions=result.total_questions,
        correct_count=result.correct_count,
        started_at=submission.submitted_at,
        submitted_at=submission.submitted_at,
        answers=[
            _answer(question.question_id, item)
            for question, item in zip(record.paper.questions, review_items(record), strict=True)
        ],
    )


def _answer(question_id: int, item: ReviewItem) -> AttemptAnswer:
    if isinstance(item, ChoiceFeedback):
        return _attempt_answer(question_id, item, (item.selected_choice, item.correct_choice), None)
    texts = (item.result.answer, item.model_answer)
    return _attempt_answer(question_id, item.result, texts, item.misconception)


def _attempt_answer(
    question_id: int,
    feedback: AnswerFeedback,
    texts: tuple[str | None, str],
    misconception: str | None,
) -> AttemptAnswer:
    given, expected = texts
    return AttemptAnswer(
        question_id=question_id,
        is_correct=feedback.is_correct,
        score=feedback.score,
        misconception=misconception,
        question_stem_snapshot=feedback.question,
        selected_choice_text_snapshot=given or "",
        correct_choice_text_snapshot=expected,
        explanation_snapshot=feedback.explanation,
    )
