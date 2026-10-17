from dataclasses import dataclass

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select

from app.models import (
    Attempt,
    AttemptAnswer,
    Choice,
    Concept,
    ConceptAssignmentRole,
    ConceptStatus,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionKeyPoint,
    QuestionStatus,
    QuestionType,
    Quiz,
    QuizQuestion,
    QuizStatus,
    QuizType,
    Subject,
)
from app.schemas import MultipleChoiceQuestionCreate, OpenEndedQuestionCreate, QuestionCreate
from app.services.concept_service import normalize_concept_name
from tests.workspace_clients import (
    create_learner,
    learner_browser,
    learner_session,
    reviewer_browser,
    reviewer_session,
)

pytestmark = pytest.mark.integration

MODEL_ANSWER = "Plants turn light, water, and carbon dioxide into sugar."
KEY_POINTS = ["Uses light energy", "Makes sugar"]
GRADING_NOTES = "Accept glucose for sugar."
EXAM_ONLY_STEM = "Which gas do plants release on exam day?"
OPEN_ENDED_STEM = "Explain how a plant makes its food."
EXAM_SECRETS = (MODEL_ANSWER, *KEY_POINTS, GRADING_NOTES, EXAM_ONLY_STEM, OPEN_ENDED_STEM)

QUESTION_CREATE = TypeAdapter(QuestionCreate)
MULTIPLE_CHOICE = {
    "stem": "Which gas do plants absorb?",
    "choices": [
        {"text": "Carbon dioxide", "is_correct": True},
        {"text": "Oxygen"},
        {"text": "Helium"},
        {"text": "Neon"},
    ],
}
OPEN_ENDED = {
    "question_type": "open_ended",
    "stem": OPEN_ENDED_STEM,
    "model_answer": MODEL_ANSWER,
    "key_points": KEY_POINTS,
    "concept_proposals": [{"name": "Photosynthesis", "role": "primary"}],
}


def test_question_create_union_parses_each_type():
    multiple_choice = QUESTION_CREATE.validate_python(MULTIPLE_CHOICE)
    open_ended = QUESTION_CREATE.validate_python(OPEN_ENDED)

    assert isinstance(multiple_choice, MultipleChoiceQuestionCreate)
    assert [choice.text for choice in multiple_choice.choices] == [
        "Carbon dioxide",
        "Oxygen",
        "Helium",
        "Neon",
    ]
    assert isinstance(open_ended, OpenEndedQuestionCreate)
    assert open_ended.key_points == ["Uses light energy", "Makes sugar"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({**OPEN_ENDED, "key_points": ["Uses light energy"]}, id="one-key-point"),
        pytest.param({**OPEN_ENDED, "key_points": ["a", "b", "c", "d", "e"]}, id="five-key-points"),
        pytest.param({**OPEN_ENDED, "choices": MULTIPLE_CHOICE["choices"]}, id="choices-on-open"),
        pytest.param({**MULTIPLE_CHOICE, "key_points": KEY_POINTS}, id="key-points-on-choice"),
        pytest.param({**MULTIPLE_CHOICE, "model_answer": MODEL_ANSWER}, id="answer-on-choice"),
        pytest.param({**OPEN_ENDED, "model_answer": "  "}, id="blank-model-answer"),
    ],
)
def test_question_create_union_rejects_mixed_payloads(payload):
    with pytest.raises(ValidationError):
        QUESTION_CREATE.validate_python(payload)


@dataclass(frozen=True)
class LearnerStudy:
    learner_id: int
    subject_id: int
    practice_quiz_id: int
    exam_quiz_id: int
    concept_id: int


def _concept(subject: Subject, name: str) -> Concept:
    return Concept(
        subject=subject,
        name=name,
        slug=name.lower().replace(" ", "-"),
        normalized_name=normalize_concept_name(name, subject_name=subject.name),
        status=ConceptStatus.ACTIVE,
    )


def _question(subject: Subject, stem: str, concept: Concept, *, exam_only: bool) -> Question:
    question = Question(
        subject=subject,
        stem=stem,
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        exam_only=exam_only,
        choices=[
            Choice(text=text, is_correct=index == 1, order_index=index)
            for index, text in enumerate(["Oxygen", "Helium", "Neon", "Argon"], start=1)
        ],
    )
    question.concept_links = [QuestionConcept(concept=concept, role=ConceptAssignmentRole.PRIMARY)]
    return question


def _open_ended_question(subject: Subject, concept: Concept) -> Question:
    question = Question(
        subject=subject,
        stem=OPEN_ENDED_STEM,
        difficulty=Difficulty.BEGINNER,
        status=QuestionStatus.ACTIVE,
        question_type=QuestionType.OPEN_ENDED,
        exam_only=True,
        model_answer=MODEL_ANSWER,
        grading_notes=GRADING_NOTES,
        key_points=[
            QuestionKeyPoint(text=text, order_index=index)
            for index, text in enumerate(KEY_POINTS, start=1)
        ],
    )
    question.concept_links = [QuestionConcept(concept=concept, role=ConceptAssignmentRole.PRIMARY)]
    return question


def _quiz(title: str, subject: Subject, questions: list[Question], quiz_type: QuizType) -> Quiz:
    return Quiz(
        title=title,
        primary_subject=subject,
        difficulty=Difficulty.BEGINNER,
        quiz_type=quiz_type,
        status=QuizStatus.PUBLISHED,
        question_links=[
            QuizQuestion(question=question, order_index=index)
            for index, question in enumerate(questions, start=1)
        ],
    )


def seed_learner_study(email: str = "child@example.com") -> LearnerStudy:
    learner_id = create_learner(email, "Child")
    subject = Subject(name="Biology", slug="biology")
    practice_concept = _concept(subject, "Plant gases")
    exam_concept = _concept(subject, "Photosynthesis")
    practice = _quiz(
        "Plant practice",
        subject,
        [_question(subject, "Which gas do plants absorb?", practice_concept, exam_only=False)],
        QuizType.STANDARD,
    )
    exam = _quiz(
        "Plant exam",
        subject,
        [
            _open_ended_question(subject, exam_concept),
            _question(subject, EXAM_ONLY_STEM, exam_concept, exam_only=True),
        ],
        QuizType.EXAM,
    )
    with reviewer_session(learner_id) as db:
        db.add_all([practice, exam])
        db.commit()
        return LearnerStudy(learner_id, subject.id, practice.id, exam.id, exam_concept.id)


def _route(study: LearnerStudy, template: str) -> str:
    return template.format(
        subject=study.subject_id,
        practice=study.practice_quiz_id,
        exam=study.exam_quiz_id,
        concept=study.concept_id,
    )


LEARNER_READ_ROUTES = [
    ("GET", "/quizzes", 200),
    ("GET", "/quizzes/{practice}", 200),
    ("GET", "/quizzes/{practice}/take", 200),
    ("GET", "/quizzes/{exam}", 404),
    ("GET", "/quizzes/{exam}/take", 404),
    ("GET", "/quizzes/{exam}/questions/new?question_type=open_ended", 404),
    ("GET", "/subjects", 200),
    ("GET", "/subjects/{subject}", 200),
    ("GET", "/mastery", 200),
    ("GET", "/progress", 200),
    ("GET", "/exams", 200),
    ("GET", "/api/quizzes", 200),
    ("GET", "/api/quizzes/{practice}", 200),
    ("GET", "/api/quizzes/{exam}", 404),
    ("GET", "/api/subjects/{subject}/concepts", 200),
    ("GET", "/api/mastery/weak-concepts", 200),
    ("POST", "/subjects/{subject}/practice", 200),
    ("POST", "/concepts/{concept}/practice", 200),
    ("POST", "/api/subjects/{subject}/practice", 201),
]


@pytest.mark.parametrize(("method", "path", "status"), LEARNER_READ_ROUTES)
def test_learner_routes_never_reveal_exam_questions(db_session, method, path, status):
    study = seed_learner_study()
    learner = learner_browser(study.learner_id)

    response = learner.request(method, _route(study, path))
    if response.status_code == 303:
        response = learner.get(response.headers["location"])

    assert (method, path, response.status_code) == (method, path, status)
    for secret in EXAM_SECRETS:
        assert secret not in response.text
    assert "Plant exam" not in response.text


def test_learner_practice_has_no_exam_questions(db_session):
    study = seed_learner_study()
    learner = learner_browser(study.learner_id)

    practice = learner.post(f"/api/subjects/{study.subject_id}/practice", json={})

    assert practice.status_code == 201
    assert [question["stem"] for question in practice.json()["questions"]] == [
        "Which gas do plants absorb?"
    ]


@pytest.mark.parametrize(
    "path",
    ["/quizzes/{exam}", "/subjects/{subject}", "/api/quizzes/{exam}"],
)
def test_reviewer_preview_shows_exam_questions_and_rubric(db_session, path):
    study = seed_learner_study()
    reviewer = reviewer_browser(study.learner_id)

    response = reviewer.get(_route(study, path))

    assert response.status_code == 200
    for secret in EXAM_SECRETS:
        assert secret in response.text


def test_learner_cannot_delete_concept_used_only_by_exam_questions(db_session):
    study = seed_learner_study()
    learner = learner_browser(study.learner_id)

    response = learner.delete(f"/api/concepts/{study.concept_id}")

    assert response.status_code == 422
    assert response.json()["detail"] == "Only empty, history-free concepts can be deleted."
    with reviewer_session(study.learner_id) as db:
        assert db.get(Concept, study.concept_id) is not None


def test_learner_mastery_counts_exam_answers(db_session):
    study = seed_learner_study()
    with learner_session(study.learner_id) as db:
        assert db.get(Quiz, study.exam_quiz_id) is None
    with reviewer_session(study.learner_id) as db:
        question = db.scalar(select(Question).where(Question.stem == EXAM_ONLY_STEM))
        assert question is not None
        db.add(
            Attempt(
                quiz_id=study.exam_quiz_id,
                session_type="exam",
                score=100,
                total_questions=1,
                correct_count=1,
                submitted_at=func.now(),
                answers=[AttemptAnswer(question_id=question.id, is_correct=True, score=1.0)],
            )
        )
        db.commit()

    mastery = learner_browser(study.learner_id).get("/mastery")

    assert mastery.status_code == 200
    assert "2 concepts · 1 attempts" in mastery.text
    assert "100% mastery" in mastery.text


def test_exam_quiz_refuses_practice_questions_and_practice_quiz_refuses_exam_questions(
    db_session,
):
    study = seed_learner_study()
    with reviewer_session(study.learner_id) as db:
        exam = db.get(Quiz, study.exam_quiz_id)
        practice_question = db.scalar(
            select(Question).where(Question.stem == "Which gas do plants absorb?")
        )
        assert exam is not None and practice_question is not None
        exam.question_links.append(QuizQuestion(question=practice_question, order_index=9))
        with pytest.raises(ValueError, match="Exam quizzes hold only exam questions"):
            db.commit()
        db.rollback()

        practice = db.get(Quiz, study.practice_quiz_id)
        exam_question = db.scalar(select(Question).where(Question.stem == EXAM_ONLY_STEM))
        assert practice is not None and exam_question is not None
        practice.question_links.append(QuizQuestion(question=exam_question, order_index=9))
        with pytest.raises(ValueError, match="Exam quizzes hold only exam questions"):
            db.commit()


def open_ended_form(**overrides: str) -> dict[str, str]:
    return {
        "question_type": "open_ended",
        "stem": OPEN_ENDED_STEM,
        "model_answer": MODEL_ANSWER,
        "key_point_1": KEY_POINTS[0],
        "key_point_2": KEY_POINTS[1],
        "key_point_3": "",
        "key_point_4": "",
        "grading_notes": GRADING_NOTES,
        "concepts": "Photosynthesis",
        **overrides,
    }


def test_reviewer_adds_open_ended_question_to_exam_quiz_through_form(db_session):
    study = seed_learner_study()
    reviewer = reviewer_browser(study.learner_id)

    form = reviewer.get(f"/quizzes/{study.exam_quiz_id}/questions/new?question_type=open_ended")
    created = reviewer.post(
        f"/quizzes/{study.exam_quiz_id}/questions",
        data=open_ended_form(stem="Why do leaves need sunlight?", key_point_3="Happens in leaves"),
    )

    assert form.status_code == 200
    assert 'name="model_answer"' in form.text
    assert created.status_code == 303
    with reviewer_session(study.learner_id) as db:
        question = db.scalar(
            select(Question).where(Question.stem == "Why do leaves need sunlight?")
        )
        assert question is not None
        assert (question.question_type, question.exam_only) == (QuestionType.OPEN_ENDED, True)
        assert question.model_answer == MODEL_ANSWER
        assert question.grading_notes == GRADING_NOTES
        assert [point.text for point in question.key_points] == [
            "Uses light energy",
            "Makes sugar",
            "Happens in leaves",
        ]
        assert question.choices == []
    detail = reviewer.get(f"/quizzes/{study.exam_quiz_id}")
    assert "Why do leaves need sunlight?" in detail.text
    assert "Happens in leaves" in detail.text


def test_exam_quiz_form_adds_exam_only_multiple_choice_question(db_session):
    study = seed_learner_study()
    reviewer = reviewer_browser(study.learner_id)

    created = reviewer.post(
        f"/quizzes/{study.exam_quiz_id}/questions",
        data={
            "question_type": "multiple_choice",
            "stem": "Which part of a plant holds chlorophyll?",
            "choice_1": "Leaf",
            "choice_2": "Root",
            "choice_3": "Seed",
            "choice_4": "Bark",
            "correct_choice": "0",
            "concepts": "Photosynthesis",
        },
    )

    assert created.status_code == 303
    with reviewer_session(study.learner_id) as db:
        question = db.scalar(
            select(Question).where(Question.stem == "Which part of a plant holds chlorophyll?")
        )
        assert question is not None
        assert (question.question_type, question.exam_only) == (
            QuestionType.MULTIPLE_CHOICE,
            True,
        )


def test_open_ended_form_errors_keep_the_open_ended_form(db_session):
    study = seed_learner_study()
    reviewer = reviewer_browser(study.learner_id)

    one_point = reviewer.post(
        f"/quizzes/{study.exam_quiz_id}/questions", data=open_ended_form(key_point_2="")
    )
    no_answer = reviewer.post(
        f"/quizzes/{study.exam_quiz_id}/questions", data=open_ended_form(model_answer=" ")
    )

    assert one_point.status_code == 422
    assert "Open-ended questions need two to four key points." in one_point.text
    assert 'name="model_answer"' in one_point.text
    assert no_answer.status_code == 422
    assert "Open-ended questions need a model answer." in no_answer.text


def test_practice_quiz_refuses_open_ended_question_on_web_and_api(client, db_session):
    before = db_session.scalar(select(func.count(Question.id)))

    form = client.get("/quizzes/1/questions/new?question_type=open_ended")
    web = client.post("/quizzes/1/questions", data=open_ended_form())
    api = client.post("/api/quizzes/1/questions", json=OPEN_ENDED)

    assert form.status_code == 200
    assert "Add a multiple-choice question" in form.text
    assert 'name="model_answer"' not in form.text
    assert web.status_code == 422
    assert "Open-ended questions belong only in exam quizzes." in web.text
    assert api.status_code == 422
    assert api.json()["detail"] == "Open-ended questions belong only in exam quizzes."
    assert db_session.scalar(select(func.count(Question.id))) == before


def test_api_adds_open_ended_question_to_exam_quiz(db_session):
    study = seed_learner_study()
    reviewer = reviewer_browser(study.learner_id)

    created = reviewer.post(
        f"/api/quizzes/{study.exam_quiz_id}/questions",
        json={**OPEN_ENDED, "grading_notes": GRADING_NOTES},
    )
    mixed = reviewer.post(
        f"/api/quizzes/{study.exam_quiz_id}/questions",
        json={**OPEN_ENDED, "choices": MULTIPLE_CHOICE["choices"]},
    )

    assert created.status_code == 201
    body = created.json()
    assert (body["question_type"], body["exam_only"]) == ("open_ended", True)
    assert body["model_answer"] == MODEL_ANSWER
    assert body["grading_notes"] == GRADING_NOTES
    assert [point["text"] for point in body["key_points"]] == KEY_POINTS
    assert body["choices"] == []
    assert mixed.status_code == 422
