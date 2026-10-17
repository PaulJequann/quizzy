from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import (
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Question,
    QuestionConcept,
    QuestionHealthState,
    QuestionType,
    Subject,
)
from app.services.concept_service import normalize_concept_name
from app.services.grading_service import get_mastery_rows
from app.services.teaching_brief_service import prepare_next_practice
from tests.test_exams import assign_exam
from tests.test_open_ended_exam_grading import (
    LEARNER_ANSWER,
    MODEL_ANSWER,
    WRITTEN_STEM,
    propose_grade,
    seed_mixed_exam,
)
from tests.workspace_clients import (
    create_learner,
    learner_browser,
    learner_session,
    reviewer_browser,
    reviewer_session,
)

pytestmark = pytest.mark.integration

ALL_KEY_POINTS = ["1", "2", "3"]
MISCONCEPTION = "thinks plants eat soil"
CONCEPT_BY_TYPE = {
    QuestionType.OPEN_ENDED: "Photosynthesis",
    QuestionType.MULTIPLE_CHOICE: "Tests",
}


@dataclass(frozen=True)
class Study:
    learner_id: int
    quiz_id: int
    reviewer: TestClient
    learner: TestClient


def linked_study() -> Study:
    learner_id = create_learner("child@example.com", "Child")
    quiz_id = seed_mixed_exam(learner_id)
    with reviewer_session(learner_id) as db:
        for question in db.scalars(select(Question)):
            concept = _concept(question.subject, CONCEPT_BY_TYPE[question.question_type])
            question.concept_links = [
                QuestionConcept(concept=concept, role=ConceptAssignmentRole.PRIMARY)
            ]
        db.commit()
    return Study(learner_id, quiz_id, reviewer_browser(learner_id), learner_browser(learner_id))


def _concept(subject: Subject, name: str) -> Concept:
    return Concept(
        name=name,
        slug=name.lower(),
        normalized_name=normalize_concept_name(name, subject_name=subject.name),
        status=ConceptStatus.ACTIVE,
        subject=subject,
    )


def submit(study: Study) -> int:
    exam_id = assign_exam(study.reviewer, study.quiz_id)
    response = study.learner.post(
        f"/exams/{exam_id}/submit", data={"answer_1": "1", "answer_2": LEARNER_ANSWER}
    )
    assert response.status_code == 303
    return exam_id


def release(study: Study, exam_id: int, met: list[str], misconception: str = "") -> None:
    response = study.reviewer.post(
        f"/exams/{exam_id}/release",
        data={
            "note": "Well done.",
            "grade_2_met": met,
            "grade_2_feedback": "Name every step.",
            "grade_2_misconception": misconception,
        },
    )
    assert response.status_code == 303


def mastery(study: Study) -> dict[str, tuple[float, int, int, int]]:
    with learner_session(study.learner_id) as db:
        return {
            row.concept.name: (
                row.conservative_performance,
                row.correct_attempts,
                row.total_attempts,
                len(row.missed_question_ids),
            )
            for row in get_mastery_rows(db)
        }


def test_open_ended_success_moves_mastery_more_than_multiple_choice_success(db_session):
    study = linked_study()

    release(study, submit(study), ALL_KEY_POINTS)

    assert mastery(study) == {
        "Photosynthesis": (50.13, 1, 1, 0),
        "Tests": (43.1, 1, 1, 0),
    }


def test_second_open_ended_success_on_same_question_counts(db_session):
    study = linked_study()
    release(study, submit(study), ALL_KEY_POINTS)

    release(study, submit(study), ALL_KEY_POINTS)

    assert mastery(study)["Photosynthesis"] == (57.96, 2, 2, 0)


def test_partial_open_ended_answer_is_partial_success_and_a_miss(db_session):
    study = linked_study()

    release(study, submit(study), ["1"])

    assert mastery(study)["Photosynthesis"] == (22.91, 0, 1, 1)


def test_open_ended_question_health_uses_mean_score(db_session):
    study = linked_study()

    for _ in range(3):
        release(study, submit(study), ["1"])

    with reviewer_session(study.learner_id) as db:
        written = db.scalar(select(Question).where(Question.stem == WRITTEN_STEM))
        assert written is not None
        assert (written.health_state, written.health_score) == (
            QuestionHealthState.HEALTHY,
            33.33,
        )


def test_reviewer_misconception_reaches_next_practice_and_not_the_learner(db_session):
    study = linked_study()
    exam_id = submit(study)
    propose_grade(study.learner_id, [1, 2], "Good start.", "thinks leaves are roots")

    release(study, exam_id, ["1", "2"], MISCONCEPTION)

    with reviewer_session(study.learner_id) as db:
        misses = prepare_next_practice(db, study.learner_id)["recent_misses"]
    assert [
        (miss["question"], miss["selected_answer"], miss["correct_answer"], miss["misconception"])
        for miss in misses
    ] == [(WRITTEN_STEM, LEARNER_ANSWER, MODEL_ANSWER, MISCONCEPTION)]
    with learner_session(study.learner_id) as db:
        assert prepare_next_practice(db, study.learner_id)["recent_misses"] == []
    teaching = study.learner.get("/teaching")
    assert teaching.status_code == 200
    assert "recent_misses" in teaching.text
    assert WRITTEN_STEM not in teaching.text
    assert MISCONCEPTION not in teaching.text


def test_correct_answer_without_misconception_is_not_a_recent_miss(db_session):
    study = linked_study()

    release(study, submit(study), ["1", "2"])

    with reviewer_session(study.learner_id) as db:
        assert prepare_next_practice(db, study.learner_id)["recent_misses"] == []
