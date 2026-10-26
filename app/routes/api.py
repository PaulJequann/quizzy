from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.boundary import study_operation
from app.database import get_db
from app.models import Concept, Question, Quiz, Subject
from app.routes import concept_api_actions
from app.schemas import (
    AttemptCreate,
    AttemptRead,
    ConceptArchiveRequest,
    ConceptCreate,
    ConceptMergeRequest,
    ConceptQueueItemRead,
    ConceptRead,
    ConceptSplitRequest,
    ConceptUpdate,
    QuestionCreate,
    QuestionRead,
    QuestionStudyRead,
    QuizCreate,
    QuizRead,
    QuizStudyRead,
)
from app.services import (
    concept_lifecycle_service,
    concept_maintenance_service,
    practice_service,
    question_health_service,
)
from app.services.concept_maintenance_service import MergeConceptsInput
from app.services.grading_service import get_concept_queue_items, submit_attempt
from app.services.quiz_service import create_question, create_quiz, get_quiz, list_quizzes
from app.study_access import StudyOperation

router = APIRouter(prefix="/api", tags=["api"])


def _quiz_view(request: Request, quiz: Quiz) -> QuizRead | QuizStudyRead:
    if request.state.access.actor_is_superuser:
        return QuizRead.model_validate(quiz)
    return QuizStudyRead.model_validate(quiz)


def _question_view(request: Request, question: Question) -> QuestionRead | QuestionStudyRead:
    if request.state.access.actor_is_superuser:
        return QuestionRead.model_validate(question)
    return QuestionStudyRead.model_validate(question)


@router.get("/quizzes", response_model=list[QuizRead | QuizStudyRead])
@study_operation(StudyOperation.MANAGE)
def api_quizzes(request: Request, db: Annotated[Session, Depends(get_db)]):
    return [_quiz_view(request, quiz) for quiz in list_quizzes(db)]


@router.get("/mastery/weak-concepts", response_model=list[ConceptQueueItemRead])
@study_operation(StudyOperation.MANAGE)
def api_weak_concepts(db: Annotated[Session, Depends(get_db)]):
    return get_concept_queue_items(db)


@router.get("/subjects/{subject_id}/concepts", response_model=list[ConceptRead])
@study_operation(StudyOperation.MANAGE)
def api_subject_concepts(subject_id: int, db: Annotated[Session, Depends(get_db)]):
    return list(
        db.scalars(
            select(Concept)
            .where(Concept.subject_id == subject_id)
            .options(selectinload(Concept.subject))
            .order_by(Concept.status, Concept.name)
        )
    )


@router.post("/subjects/{subject_id}/concepts", response_model=ConceptRead, status_code=201)
@study_operation(StudyOperation.MANAGE)
def api_create_subject_concept(
    subject_id: int,
    payload: ConceptCreate,
    db: Annotated[Session, Depends(get_db)],
):
    subject = _require_subject(db, subject_id)
    return _run_concept_action(
        lambda: concept_lifecycle_service.create_concept(
            db,
            concept_lifecycle_service.CreateConceptRequest(
                subject=subject,
                name=payload.name,
                parent=_parent_concept(db, subject_id, payload.parent_concept_id),
                status=payload.status,
                rationale=payload.rationale,
            ),
        )
    )


@router.patch("/concepts/{concept_id}", response_model=ConceptRead)
@study_operation(StudyOperation.MANAGE)
def api_update_concept(
    concept_id: int,
    payload: ConceptUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    concept = _require_concept(db, concept_id)
    parent = None
    if "parent_concept_id" in payload.model_fields_set:
        parent = _run_concept_action(
            lambda: _parent_concept(db, concept.subject_id, payload.parent_concept_id)
        )
        _run_concept_action(
            lambda: concept_lifecycle_service._validate_parent_move(concept, parent)
        )
    if payload.name is not None:
        name = payload.name
        concept = _run_concept_action(
            lambda: concept_lifecycle_service.rename_concept(
                db,
                concept,
                name=name,
                rationale=payload.rationale,
            )
        )
    if "parent_concept_id" in payload.model_fields_set:
        concept = _run_concept_action(
            lambda: concept_lifecycle_service.move_concept(
                db,
                concept,
                parent=parent,
                rationale=payload.rationale,
            )
        )
    return concept


@router.post("/concepts/{concept_id}/split")
@study_operation(StudyOperation.MANAGE)
def api_split_concept(
    concept_id: int,
    payload: ConceptSplitRequest,
    db: Annotated[Session, Depends(get_db)],
):
    concept = _require_concept(db, concept_id)
    split_request = concept_lifecycle_service.SplitConceptRequest(
        concept=concept,
        child_names=payload.child_names,
        question_ids_by_child_name=payload.question_ids_by_child_name,
        rationale=payload.rationale,
    )
    return _run_concept_action(lambda: concept_lifecycle_service.split_concept(db, split_request))


@router.post("/concepts/{concept_id}/archive", response_model=ConceptRead)
@study_operation(StudyOperation.MANAGE)
def api_archive_concept(
    concept_id: int,
    payload: ConceptArchiveRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return _run_concept_action(
        lambda: concept_api_actions.archive_concept(
            db,
            _require_concept(db, concept_id),
            payload,
        )
    )


@router.delete("/concepts/{concept_id}", status_code=204)
@study_operation(StudyOperation.MANAGE)
def api_delete_empty_concept(
    concept_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    concept = _require_concept(db, concept_id)
    _run_concept_action(lambda: concept_lifecycle_service.delete_empty_concept(db, concept))
    return Response(status_code=204)


@router.post("/concepts/merge")
@study_operation(StudyOperation.MANAGE)
def api_merge_concepts(
    payload: ConceptMergeRequest,
    db: Annotated[Session, Depends(get_db)],
):
    target = _require_concept(db, payload.target_concept_id)
    source_names = [
        _require_concept(db, source_id).name for source_id in payload.source_concept_ids
    ]
    return _run_concept_action(
        lambda: concept_maintenance_service.merge_concepts(
            db,
            MergeConceptsInput(
                subject_name=target.subject.name,
                target_concept_name=target.name,
                source_concept_names=source_names,
                dry_run=payload.dry_run,
            ),
        )
    )


@router.get("/quizzes/{quiz_id}", response_model=QuizRead | QuizStudyRead)
@study_operation(StudyOperation.MANAGE)
def api_quiz(request: Request, quiz_id: int, db: Annotated[Session, Depends(get_db)]):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return _quiz_view(request, quiz)


@router.post("/quizzes", response_model=QuizRead | QuizStudyRead, status_code=201)
@study_operation(StudyOperation.MANAGE)
def api_create_quiz(request: Request, payload: QuizCreate, db: Annotated[Session, Depends(get_db)]):
    return _quiz_view(request, create_quiz(db, payload))


@router.post(
    "/quizzes/{quiz_id}/questions",
    response_model=QuestionRead | QuestionStudyRead,
    status_code=201,
)
@study_operation(StudyOperation.MANAGE)
def api_create_question(
    request: Request,
    quiz_id: int,
    payload: QuestionCreate,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    try:
        return _question_view(request, create_question(db, quiz, payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptRead, status_code=201)
@study_operation(StudyOperation.MANAGE_AND_RECORD)
def api_create_attempt(
    quiz_id: int,
    payload: AttemptCreate,
    db: Annotated[Session, Depends(get_db)],
):
    quiz = get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    answers = {answer.question_id: answer.selected_choice_id for answer in payload.answers}
    try:
        attempt = submit_attempt(db, quiz, answers)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    question_health_service.record_attempt_evidence(db, attempt.id)
    return attempt


@router.post(
    "/subjects/{subject_id}/practice",
    response_model=QuizRead | QuizStudyRead,
    status_code=201,
)
@study_operation(StudyOperation.MANAGE)
def api_create_subject_practice(
    request: Request,
    subject_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        quiz = practice_service.assemble_practice_session(
            db,
            practice_service.PracticeAssemblyInput(subject_id=subject_id),
        )
        return _quiz_view(request, quiz)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _require_concept(db: Session, concept_id: int) -> Concept:
    concept = db.scalar(
        select(Concept)
        .where(Concept.id == concept_id)
        .options(selectinload(Concept.subject), selectinload(Concept.parent))
    )
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    return concept


def _require_subject(db: Session, subject_id: int) -> Subject:
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


def _run_concept_action[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _parent_concept(db: Session, subject_id: int, parent_id: int | None) -> Concept | None:
    if parent_id is None:
        return None
    parent = _require_concept(db, parent_id)
    if parent.subject_id != subject_id:
        raise ValueError("Parent concept must belong to the same subject.")
    return parent
