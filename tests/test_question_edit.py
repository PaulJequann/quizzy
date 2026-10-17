import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import AttemptAnswer, Concept, ConceptStatus, Question, QuestionType, Quiz
from app.services.grading_service import submit_attempt
from tests.test_open_ended_questions import (
    EXAM_ONLY_STEM,
    GRADING_NOTES,
    KEY_POINTS,
    MODEL_ANSWER,
    OPEN_ENDED_STEM,
    LearnerStudy,
    open_ended_form,
    seed_learner_study,
)
from tests.workspace_clients import (
    learner_browser,
    learner_session,
    reviewer_browser,
    reviewer_session,
)

pytestmark = pytest.mark.integration

PRACTICE_STEM = "Which gas do plants absorb?"


def question_id(study: LearnerStudy, stem: str) -> int:
    with reviewer_session(study.learner_id) as db:
        found = db.scalar(select(Question.id).where(Question.stem == stem))
        assert found is not None
        return found


def stored_question(study: LearnerStudy, stem: str) -> Question:
    with reviewer_session(study.learner_id) as db:
        question = db.scalar(
            select(Question)
            .options(
                selectinload(Question.choices),
                selectinload(Question.key_points),
                selectinload(Question.concepts),
            )
            .where(Question.stem == stem)
        )
        assert question is not None
        return question


def answer_practice_question(study: LearnerStudy) -> int:
    with learner_session(study.learner_id) as db:
        quiz = db.get(Quiz, study.practice_quiz_id)
        assert quiz is not None
        question = quiz.questions[0]
        submit_attempt(db, quiz, {question.id: question.choices[1].id})
        answer = db.scalar(select(AttemptAnswer))
        assert answer is not None and answer.selected_choice_id is not None
        return answer.selected_choice_id


def multiple_choice_form(**overrides: str) -> dict[str, str]:
    return {
        "question_type": "multiple_choice",
        "stem": "Which gas do plants take in?",
        "choice_1": "Carbon dioxide",
        "choice_2": "Oxygen",
        "choice_3": "Helium",
        "choice_4": "Neon",
        "correct_choice": "0",
        "explanation": "Leaves take in carbon dioxide.",
        "source_reference": "biology/plants.md#gases",
        "concepts": "Plant gases",
        **overrides,
    }


def test_reviewer_edits_multiple_choice_question_through_form(db_session):
    study = seed_learner_study()
    selected_choice_id = answer_practice_question(study)
    before = stored_question(study, PRACTICE_STEM)
    reviewer = reviewer_browser(study.learner_id)
    edit_path = f"/quizzes/{study.practice_quiz_id}/questions/{before.id}"

    detail = reviewer.get(f"/quizzes/{study.practice_quiz_id}")
    form = reviewer.get(f"{edit_path}/edit")
    saved = reviewer.post(
        edit_path, data=multiple_choice_form(concepts="Plant gases, Photosynthesis")
    )

    assert f'href="{edit_path}/edit"' in detail.text
    assert form.status_code == 200
    assert "Edit a multiple-choice question" in form.text
    assert f'action="{edit_path}"' in form.text
    assert f">{PRACTICE_STEM}</textarea>" in form.text
    assert 'name="choice_2" value="Helium"' in form.text
    assert 'value="Plant gases"' in form.text
    assert (saved.status_code, saved.headers["location"]) == (
        303,
        f"/quizzes/{study.practice_quiz_id}",
    )
    after = stored_question(study, "Which gas do plants take in?")
    assert after.id == before.id
    assert [(choice.id, choice.text, choice.is_correct) for choice in after.choices] == [
        (before.choices[0].id, "Carbon dioxide", True),
        (before.choices[1].id, "Oxygen", False),
        (before.choices[2].id, "Helium", False),
        (before.choices[3].id, "Neon", False),
    ]
    assert (after.explanation, after.source_reference) == (
        "Leaves take in carbon dioxide.",
        "biology/plants.md#gases",
    )
    assert (after.question_type, after.exam_only) == (QuestionType.MULTIPLE_CHOICE, False)
    assert [concept.name for concept in after.concepts] == ["Photosynthesis", "Plant gases"]
    with reviewer_session(study.learner_id) as db:
        assert db.scalar(select(AttemptAnswer.selected_choice_id)) == selected_choice_id


def test_reviewer_edits_open_ended_question_through_form(db_session):
    study = seed_learner_study()
    edited_id = question_id(study, OPEN_ENDED_STEM)
    reviewer = reviewer_browser(study.learner_id)
    edit_path = f"/quizzes/{study.exam_quiz_id}/questions/{edited_id}"

    detail = reviewer.get(f"/quizzes/{study.exam_quiz_id}")
    form = reviewer.get(f"{edit_path}/edit")
    saved = reviewer.post(
        edit_path,
        data=open_ended_form(
            question_type="multiple_choice",
            stem="Explain how a leaf makes sugar.",
            model_answer="Chlorophyll captures light to turn water and carbon dioxide into sugar.",
            key_point_1="Captures light",
            key_point_2="Uses carbon dioxide",
            key_point_3="Makes sugar",
            grading_notes="Accept glucose or starch.",
        ),
    )

    assert f'href="{edit_path}/edit"' in detail.text
    assert form.status_code == 200
    assert "Edit an open-ended question" in form.text
    assert 'aria-label="Question type"' not in form.text
    assert f">{MODEL_ANSWER}</textarea>" in form.text
    assert f'name="key_point_2" value="{KEY_POINTS[1]}"' in form.text
    assert f">{GRADING_NOTES}</textarea>" in form.text
    assert saved.status_code == 303
    after = stored_question(study, "Explain how a leaf makes sugar.")
    assert after.id == edited_id
    assert (after.question_type, after.exam_only) == (QuestionType.OPEN_ENDED, True)
    assert after.model_answer == (
        "Chlorophyll captures light to turn water and carbon dioxide into sugar."
    )
    assert [point.text for point in after.key_points] == [
        "Captures light",
        "Uses carbon dioxide",
        "Makes sugar",
    ]
    assert after.grading_notes == "Accept glucose or starch."
    assert after.choices == []


@pytest.mark.parametrize(
    "key_points",
    [
        pytest.param({"key_point_2": ""}, id="one"),
        pytest.param(
            {"key_point_3": "c", "key_point_4": "d", "key_point_5": "e"},
            id="five",
        ),
    ],
)
def test_open_ended_edit_errors_rerender_the_edit_form(db_session, key_points):
    study = seed_learner_study()
    edited_id = question_id(study, OPEN_ENDED_STEM)
    reviewer = reviewer_browser(study.learner_id)
    edit_path = f"/quizzes/{study.exam_quiz_id}/questions/{edited_id}"

    saved = reviewer.post(
        edit_path, data=open_ended_form(stem="Explain photosynthesis again.", **key_points)
    )

    assert saved.status_code == 422
    assert "Open-ended questions need two to four key points." in saved.text
    assert "Edit an open-ended question" in saved.text
    assert f'action="{edit_path}"' in saved.text
    assert ">Explain photosynthesis again.</textarea>" in saved.text
    unchanged = stored_question(study, OPEN_ENDED_STEM)
    assert [point.text for point in unchanged.key_points] == KEY_POINTS


def test_learner_cannot_open_or_save_exam_question_edits(db_session):
    study = seed_learner_study()
    exam_ids = [question_id(study, OPEN_ENDED_STEM), question_id(study, EXAM_ONLY_STEM)]
    learner = learner_browser(study.learner_id)

    responses = [
        response.status_code
        for quiz_id in (study.exam_quiz_id, study.practice_quiz_id)
        for exam_id in exam_ids
        for response in (
            learner.get(f"/quizzes/{quiz_id}/questions/{exam_id}/edit"),
            learner.post(
                f"/quizzes/{quiz_id}/questions/{exam_id}",
                data=open_ended_form(stem="Leaked and changed"),
            ),
        )
    ]

    assert responses == [404] * 8
    assert [stored_question(study, OPEN_ENDED_STEM).id, question_id(study, EXAM_ONLY_STEM)] == (
        exam_ids
    )


def test_edit_refuses_a_question_from_another_quiz(db_session):
    study = seed_learner_study()
    practice_id = question_id(study, PRACTICE_STEM)
    reviewer = reviewer_browser(study.learner_id)

    form = reviewer.get(f"/quizzes/{study.exam_quiz_id}/questions/{practice_id}/edit")
    saved = reviewer.post(
        f"/quizzes/{study.exam_quiz_id}/questions/{practice_id}", data=multiple_choice_form()
    )

    assert (form.status_code, saved.status_code) == (404, 404)
    assert stored_question(study, PRACTICE_STEM).id == practice_id


def test_edit_with_an_unreadable_correct_choice_keeps_the_stored_question(db_session):
    study = seed_learner_study()
    edited_id = question_id(study, PRACTICE_STEM)
    reviewer = reviewer_browser(study.learner_id)

    saved = reviewer.post(
        f"/quizzes/{study.practice_quiz_id}/questions/{edited_id}",
        data=multiple_choice_form(correct_choice="first"),
    )

    assert saved.status_code == 422
    assert "Invalid correct choice." in saved.text
    assert f">{PRACTICE_STEM}</textarea>" in saved.text
    assert stored_question(study, PRACTICE_STEM).id == edited_id


def test_edit_keeps_unchanged_proposed_concepts(db_session):
    study = seed_learner_study()
    edited_id = question_id(study, OPEN_ENDED_STEM)
    with reviewer_session(study.learner_id) as db:
        concept = db.get(Concept, study.concept_id)
        assert concept is not None
        concept.status = ConceptStatus.PROPOSED
        db.commit()
    reviewer = reviewer_browser(study.learner_id)

    saved = reviewer.post(
        f"/quizzes/{study.exam_quiz_id}/questions/{edited_id}",
        data=open_ended_form(stem="Explain how a leaf makes sugar."),
    )

    assert saved.status_code == 303
    after = stored_question(study, "Explain how a leaf makes sugar.")
    assert [(concept.name, concept.status) for concept in after.concepts] == [
        ("Photosynthesis", ConceptStatus.PROPOSED)
    ]
