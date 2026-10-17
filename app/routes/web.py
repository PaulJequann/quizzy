import random
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.boundary import require_published_for_learner, study_operation
from app.database import get_db
from app.models import (
    Attempt,
    AttemptAnswer,
    Concept,
    ConceptReviewItem,
    ConceptStatus,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionType,
    Quiz,
    QuizStatus,
    QuizType,
)
from app.services import (
    concept_lifecycle_service,
    concept_service,
    practice_service,
    question_health_service,
)
from app.services.grading_service import (
    build_concept_queue_items,
    get_mastery_rows,
    letter_grade,
    missed_concepts_for_attempt,
    passed,
    submit_attempt,
)
from app.services.quiz_service import (
    QuestionFormInput,
    create_question,
    form_question_create,
    get_quiz,
    list_quizzes,
    update_quiz_status,
)
from app.services.subject_service import (
    build_subject_mastery_groups,
    build_subject_summaries,
    get_subject,
    get_subjects,
    quizzes_for_subject,
)
from app.study_access import StudyOperation
from app.templating import templates

router = APIRouter()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def redirect_to_practice(factory: Callable[[], Quiz], fallback_path: str) -> RedirectResponse:
    try:
        quiz = factory()
    except ValueError:
        return redirect(fallback_path)
    return redirect(f"/quizzes/{quiz.id}/take")


def shuffled_quiz_steps(quiz: Quiz) -> list[dict]:
    return [
        {"question": question, "choices": random.sample(question.choices, len(question.choices))}
        for question in random.sample(quiz.questions, len(quiz.questions))
    ]


def practice_builder_context(db: Session, *, error: str | None = None) -> dict:
    subjects = get_subjects(db)
    return {
        "subjects": subjects,
        "difficulties": [difficulty for difficulty in Difficulty if difficulty != Difficulty.MIXED],
        "count_options": [5, 10, 20],
        "error": error,
    }


def practice_builder_input(form) -> practice_service.PracticeAssemblyInput:
    subject_id = form_int(form, "subject_id")
    if subject_id <= 0:
        raise ValueError("Choose a subject to practice.")
    concept_ids = form_int_list(form, "concept_ids")
    difficulty = form_difficulty(form_text(form, "difficulty"))
    return practice_service.PracticeAssemblyInput(
        subject_id=subject_id,
        count=form_count(form_int(form, "count", practice_service.DEFAULT_PRACTICE_COUNT)),
        strategy="concept_drill" if concept_ids else "random_subject",
        concept_ids=concept_ids or None,
        difficulty=difficulty,
    )


def form_difficulty(value: str) -> Difficulty | None:
    if not value:
        return None
    difficulty = Difficulty(value)
    if difficulty == Difficulty.MIXED:
        raise ValueError("Choose a specific difficulty or Any.")
    return difficulty


def form_count(value: int) -> int:
    return value if value in {5, 10, 20} else practice_service.DEFAULT_PRACTICE_COUNT


def form_text(form, key: str, default: str = "") -> str:
    value = form.get(key, default)
    return value if isinstance(value, str) else default


def form_int(form, key: str, default: int = 0) -> int:
    value = form.get(key, str(default))
    if not isinstance(value, str):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {key.replace('_', ' ')}.") from exc


def form_optional_int(form, key: str) -> int | None:
    value = form.get(key, "")
    if value == "" or value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {key.replace('_', ' ')}.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {key.replace('_', ' ')}.") from exc


def form_int_list(form, key: str) -> list[int]:
    values: list[int] = []
    for value in form.getlist(key):
        if not isinstance(value, str):
            continue
        try:
            values.append(int(value))
        except ValueError as exc:
            raise ValueError(f"Invalid {key.replace('_', ' ')}.") from exc
    return values


@router.get("/", include_in_schema=False)
@study_operation(StudyOperation.VIEW)
def root() -> RedirectResponse:
    return redirect("/quizzes")


@router.get("/quizzes", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def quiz_list(request: Request, db: Annotated[Session, Depends(get_db)]):
    quizzes = list_quizzes(db)
    quizzes = [quiz for quiz in quizzes if request.state.access.can_view_quiz(quiz.status)]
    return templates.TemplateResponse(
        request,
        "quizzes/list.html"
        if request.state.access.can_manage_study
        else "quizzes/learner_list.html",
        {"quizzes": quizzes},
    )


@router.get("/mastery", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def mastery(request: Request, db: Annotated[Session, Depends(get_db)]):
    mastery_rows = get_mastery_rows(db)
    mastery_groups = build_subject_mastery_groups(mastery_rows)
    return templates.TemplateResponse(
        request,
        "mastery/index.html",
        {
            "mastery_rows": mastery_rows,
            "queue_items": build_concept_queue_items(mastery_rows),
            "mastery_groups": mastery_groups,
        },
    )


@router.post("/mastery/review")
@study_operation(StudyOperation.MANAGE)
def create_mastery_review_quiz(db: Annotated[Session, Depends(get_db)]):
    return redirect_to_practice(lambda: practice_service.assemble_mastery_review(db), "/mastery")


@router.get("/subjects", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def subject_list(request: Request, db: Annotated[Session, Depends(get_db)]):
    mastery_groups = build_subject_mastery_groups(get_mastery_rows(db))
    subjects = get_subjects(db)
    return templates.TemplateResponse(
        request,
        "subjects/list.html",
        {"subject_summaries": build_subject_summaries(subjects, mastery_groups)},
    )


@router.get("/subjects/{subject_id}", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def subject_detail(request: Request, subject_id: int, db: Annotated[Session, Depends(get_db)]):
    subject = get_subject(db, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    mastery_rows = get_mastery_rows(db, subject_id=subject.id)
    mastery_groups = build_subject_mastery_groups(mastery_rows)
    mastery_group = mastery_groups[0] if mastery_groups else None
    concept_review_items = concept_service.list_open_concept_reviews(db, subject_id=subject.id)
    remediation_concepts = _remediation_concepts(subject.concepts)
    return templates.TemplateResponse(
        request,
        "subjects/detail.html",
        {
            "subject": subject,
            "mastery_group": mastery_group,
            "quizzes": quizzes_for_subject(subject),
            "concept_review_items": concept_review_items,
            "remediation_concepts": remediation_concepts,
            "concept_review_count_by_concept_id": _concept_review_counts(concept_review_items),
            "question_review_items": question_health_service.list_question_review_queue(
                db,
                subject_id=subject.id,
            ),
            "concept_parent_options_by_concept_id": _concept_parent_options_by_concept_id(
                subject.concepts
            ),
        },
    )


def _concept_parent_options_by_concept_id(concepts: list[Concept]) -> dict[int, list[Concept]]:
    active_concepts = [concept for concept in concepts if concept.status == ConceptStatus.ACTIVE]
    parent_id_by_id = {concept.id: concept.parent_concept_id for concept in active_concepts}
    return {
        concept.id: [
            option
            for option in active_concepts
            if option.id != concept.id
            and not _is_descendant(option.id, concept.id, parent_id_by_id)
        ]
        for concept in active_concepts
    }


def _is_descendant(
    candidate_id: int,
    ancestor_id: int,
    parent_id_by_id: dict[int, int | None],
) -> bool:
    parent_id = parent_id_by_id.get(candidate_id)
    while parent_id is not None:
        if parent_id == ancestor_id:
            return True
        parent_id = parent_id_by_id.get(parent_id)
    return False


def _remediation_concepts(concepts: list[Concept]) -> list[Concept]:
    return [
        concept
        for concept in concepts
        if concept.status in {ConceptStatus.ACTIVE, ConceptStatus.PROPOSED}
    ]


def _concept_review_counts(review_items: list[dict]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for item in review_items:
        concept_id = item.get("concept_id")
        if concept_id is not None:
            counts[concept_id] = counts.get(concept_id, 0) + 1
    return counts


@router.post("/concept-reviews/{review_item_id}/approve")
@study_operation(StudyOperation.MANAGE)
def approve_concept_review(
    review_item_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    item = _concept_review_item(db, review_item_id)
    if item.concept is None or item.concept.status != ConceptStatus.PROPOSED:
        raise HTTPException(status_code=400, detail="Only proposed concepts can be approved.")
    subject_id = item.subject_id
    try:
        concept_lifecycle_service.approve_proposed_concept(db, item.concept)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return redirect(f"/subjects/{subject_id}")


@router.post("/concept-reviews/{review_item_id}/reject")
@study_operation(StudyOperation.MANAGE)
def reject_concept_review(
    review_item_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    item = _concept_review_item(db, review_item_id)
    if item.concept is None or item.concept.status != ConceptStatus.PROPOSED:
        raise HTTPException(status_code=400, detail="Only proposed concepts can be rejected.")
    subject_id = item.subject_id
    concept_lifecycle_service.reject_proposed_concept(db, item.concept)
    return redirect(f"/subjects/{subject_id}")


@router.post("/concept-reviews/{review_item_id}/accept-assignment")
@study_operation(StudyOperation.MANAGE)
def accept_concept_review_assignment(
    review_item_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    item = _concept_review_item(db, review_item_id)
    subject_id = item.subject_id
    try:
        concept_lifecycle_service.accept_review_assignment(db, item)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return redirect(f"/subjects/{subject_id}")


@router.post("/concept-reviews/{review_item_id}/remap")
@study_operation(StudyOperation.MANAGE)
async def remap_concept_review_assignment(
    request: Request,
    review_item_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    item = _concept_review_item(db, review_item_id)
    subject_id = item.subject_id
    form = await request.form()
    try:
        target = _review_target_concept(db, form_int(form, "target_concept_id"), subject_id)
        concept_lifecycle_service.remap_review_assignment(db, item, target=target)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return redirect(f"/subjects/{subject_id}")


def _review_target_concept(db: Session, concept_id: int, subject_id: int) -> Concept:
    concept = db.get(Concept, concept_id)
    if concept is None:
        raise ValueError("Choose a target concept.")
    if concept.subject_id != subject_id:
        raise ValueError("Target concept must belong to the same subject.")
    if concept.status == ConceptStatus.ARCHIVED:
        raise ValueError("Archived concepts cannot receive review assignments.")
    return concept


def _subject_concept(db: Session, concept_id: int) -> Concept:
    concept = db.scalar(
        select(Concept)
        .where(Concept.id == concept_id)
        .options(selectinload(Concept.subject), selectinload(Concept.parent))
    )
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    return concept


def _parent_concept(db: Session, subject_id: int, parent_id: int | None) -> Concept | None:
    if parent_id is None:
        return None
    parent = db.get(Concept, parent_id)
    if parent is None:
        raise ValueError("Choose a parent concept from this subject.")
    if parent.subject_id != subject_id:
        raise ValueError("Parent concept must belong to the same subject.")
    if parent.status == ConceptStatus.ARCHIVED:
        raise ValueError("Archived concepts cannot be used as parents.")
    return parent


def _run_web_concept_action[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _concept_review_item(db: Session, review_item_id: int) -> ConceptReviewItem:
    item = db.scalar(
        select(ConceptReviewItem)
        .where(ConceptReviewItem.id == review_item_id)
        .options(
            selectinload(ConceptReviewItem.concept),
            selectinload(ConceptReviewItem.subject),
            selectinload(ConceptReviewItem.question)
            .selectinload(Question.concept_links)
            .selectinload(QuestionConcept.concept),
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Concept review item not found")
    return item


@router.post("/concepts/{concept_id}/update")
@study_operation(StudyOperation.MANAGE)
async def update_concept_from_subject(
    request: Request,
    concept_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    concept = _subject_concept(db, concept_id)
    subject_id = concept.subject_id
    form = await request.form()
    name = form_text(form, "name").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Concept name is required.")
    rationale = form_text(form, "rationale")
    parent = _run_web_concept_action(
        lambda: _parent_concept(db, subject_id, form_optional_int(form, "parent_concept_id"))
    )
    old_parent_id = concept.parent_concept_id
    new_parent_id = parent.id if parent else None
    _run_web_concept_action(
        lambda: concept_lifecycle_service._validate_parent_move(concept, parent)
    )
    if name != concept.name:
        concept = _run_web_concept_action(
            lambda: concept_lifecycle_service.rename_concept(
                db,
                concept,
                name=name,
                rationale=rationale,
            )
        )
    if new_parent_id != old_parent_id:
        _run_web_concept_action(
            lambda: concept_lifecycle_service.move_concept(
                db,
                concept,
                parent=parent,
                rationale=rationale,
            )
        )
    return redirect(f"/subjects/{subject_id}")


@router.post("/subjects/{subject_id}/practice")
@study_operation(StudyOperation.MANAGE)
def create_subject_practice(
    subject_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    return redirect_to_practice(
        lambda: practice_service.assemble_practice_session(
            db,
            practice_service.PracticeAssemblyInput(subject_id=subject_id),
        ),
        f"/subjects/{subject_id}",
    )


@router.post("/subjects/{subject_id}/review")
@study_operation(StudyOperation.MANAGE)
def create_subject_review_quiz(
    subject_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    subject = get_subject(db, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")

    return redirect_to_practice(
        lambda: practice_service.assemble_mastery_review(db, subject_id=subject.id),
        f"/subjects/{subject.id}",
    )


@router.post("/concepts/{concept_id}/practice")
@study_operation(StudyOperation.MANAGE)
def create_concept_practice(
    concept_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    concept = db.get(Concept, concept_id)
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    return redirect_to_practice(
        lambda: practice_service.assemble_concept_drill(
            db,
            concept_ids=[concept.id],
            count=10,
        ),
        f"/subjects/{concept.subject_id}",
    )


@router.get("/quizzes/new", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def new_quiz(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request,
        "quizzes/new.html",
        practice_builder_context(db),
    )


@router.post("/quizzes")
@study_operation(StudyOperation.MANAGE)
async def post_quiz(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    form = await request.form()
    try:
        practice_input = practice_builder_input(form)
        quiz = practice_service.assemble_practice_session(db, practice_input)
    except ValueError as error:
        return templates.TemplateResponse(
            request,
            "quizzes/new.html",
            practice_builder_context(db, error=str(error)),
            status_code=422,
        )
    return redirect(f"/quizzes/{quiz.id}/take")


@router.get("/quizzes/{quiz_id}", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def quiz_detail(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    if not request.state.access.can_manage_study:
        return redirect(f"/quizzes/{quiz_id}/take")
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return templates.TemplateResponse(
        request,
        "quizzes/detail.html",
        {"quiz": quiz, "status_options": list(QuizStatus)},
    )


@router.post("/quizzes/{quiz_id}/publish", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def publish_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    update_quiz_status(db, quiz, QuizStatus.PUBLISHED)
    return templates.TemplateResponse(request, "quizzes/_status_badge.html", {"quiz": quiz})


@router.get("/quizzes/{quiz_id}/questions/new", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
def new_question(
    request: Request,
    quiz_id: int,
    db: Annotated[Session, Depends(get_db)],
    question_type: str = "",
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return templates.TemplateResponse(
        request,
        "quizzes/question_form.html",
        {"quiz": quiz, "error": None, "question_type": _form_question_type(quiz, question_type)},
    )


@router.post("/quizzes/{quiz_id}/questions", response_class=HTMLResponse)
@study_operation(StudyOperation.MANAGE)
async def post_question(
    request: Request,
    quiz_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    form = await request.form()
    question_type = _form_question_type(quiz, form_text(form, "question_type"))
    try:
        create_question(db, quiz, form_question_create(_question_form_input(form)))
    except ValueError as error:
        return templates.TemplateResponse(
            request,
            "quizzes/question_form.html",
            {"quiz": quiz, "error": str(error), "question_type": question_type},
            status_code=422,
        )

    if request.headers.get("HX-Request"):
        refreshed_quiz = get_quiz(db, quiz_id)
        return templates.TemplateResponse(
            request,
            "quizzes/_question_workspace.html",
            {"quiz": refreshed_quiz, "error": None, "question_type": question_type},
        )
    return redirect(f"/quizzes/{quiz_id}")


def _form_question_type(quiz: Quiz, requested: str) -> QuestionType:
    if quiz.quiz_type == QuizType.EXAM and requested == QuestionType.OPEN_ENDED:
        return QuestionType.OPEN_ENDED
    return QuestionType.MULTIPLE_CHOICE


def _question_form_input(form) -> QuestionFormInput:
    return QuestionFormInput(
        stem=form_text(form, "stem"),
        choices=[form_text(form, f"choice_{number}") for number in range(1, 5)],
        correct_choice=form_int(form, "correct_choice"),
        explanation=form_text(form, "explanation"),
        source_reference=form_text(form, "source_reference"),
        concepts=form_text(form, "concepts"),
        question_type=QuestionType(form_text(form, "question_type", QuestionType.MULTIPLE_CHOICE)),
        model_answer=form_text(form, "model_answer"),
        grading_notes=form_text(form, "grading_notes") or None,
        key_points=[form_text(form, f"key_point_{number}") for number in range(1, 5)],
    )


@router.get("/quizzes/{quiz_id}/take", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def take_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    require_published_for_learner(request, quiz)
    if not quiz.questions:
        raise HTTPException(status_code=400, detail="Quiz has no questions")
    return templates.TemplateResponse(
        request,
        "quizzes/take.html",
        {"quiz": quiz, "question_steps": shuffled_quiz_steps(quiz)},
    )


@router.post("/quizzes/{quiz_id}/submit")
@study_operation(StudyOperation.RECORD_PROGRESS)
async def submit_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    require_published_for_learner(request, quiz)
    form = await request.form()
    answers: dict[int, int | None] = {}
    for question in quiz.questions:
        raw_answer = form.get(f"question_{question.id}")
        answers[question.id] = int(raw_answer) if isinstance(raw_answer, str) else None
    try:
        attempt = submit_attempt(db, quiz, answers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    question_health_service.record_attempt_evidence(db, attempt.id)
    return redirect(f"/attempts/{attempt.id}/results")


@router.get("/attempts/{attempt_id}/results", response_class=HTMLResponse)
@study_operation(StudyOperation.VIEW)
def attempt_results(request: Request, attempt_id: int, db: Annotated[Session, Depends(get_db)]):
    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.id == attempt_id)
        .options(
            selectinload(Attempt.quiz).selectinload(Quiz.primary_subject),
            selectinload(Attempt.answers)
            .selectinload(AttemptAnswer.question)
            .selectinload(Question.choices),
            selectinload(Attempt.answers)
            .selectinload(AttemptAnswer.question)
            .selectinload(Question.concepts),
            selectinload(Attempt.answers).selectinload(AttemptAnswer.selected_choice),
        )
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    return templates.TemplateResponse(
        request,
        "attempts/results.html",
        {
            "attempt": attempt,
            "missed_concepts": missed_concepts_for_attempt(attempt),
            "letter_grade": letter_grade(attempt.score),
            "passed": passed(attempt.score),
        },
    )
