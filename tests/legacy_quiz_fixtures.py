from __future__ import annotations

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import (
    Choice,
    Concept,
    Difficulty,
    Question,
    QuestionConcept,
    QuizQuestion,
    Subject,
)
from app.services import generation_service
from app.services.generation_service import GeneratedQuizCreateInput
from app.services.quiz_service import get_or_create_subject, slugify


def seed_legacy_route53_concepts(questions: list[dict]) -> dict:
    return _seed_legacy_route53_quiz(
        "Route 53 cleanup",
        _concepts_by_question(questions),
        questions,
    )


def seed_legacy_route53_duplicate_links(question: dict) -> dict:
    return _seed_legacy_route53_quiz(
        "Route 53 duplicate links",
        [["HTTP health checks", "TCP health checks"]],
        [question],
    )


def _concepts_by_question(questions: list[dict]) -> list[list[str]]:
    return [list(question["concept_names"]) for question in questions]


def _seed_legacy_route53_quiz(
    title: str,
    concept_names_by_question: list[list[str]],
    questions: list[dict],
) -> dict:
    with Session(get_engine()) as db:
        subject = get_or_create_subject(db, "AWS")
        quiz = generation_service.create_generated_quiz(
            db,
            GeneratedQuizCreateInput(title=title, subject_name="AWS"),
        )
        concepts = _legacy_concepts(subject, concept_names_by_question)
        db.add_all(concepts.values())
        for order_index, (question_data, concept_names) in enumerate(
            zip(questions, concept_names_by_question, strict=True),
            start=1,
        ):
            question = _legacy_question(subject, question_data, concepts, concept_names)
            db.add(question)
            quiz.question_links.append(
                QuizQuestion(
                    question=question,
                    order_index=order_index,
                    selection_reason="legacy granular concept fixture",
                )
            )
        db.commit()
        return generation_service.generated_quiz_to_dict(db, quiz.id)


def _legacy_concepts(
    subject: Subject,
    concept_names_by_question: list[list[str]],
) -> dict[str, Concept]:
    concept_names = {
        concept_name
        for concept_names in concept_names_by_question
        for concept_name in concept_names
    }
    return {
        concept_name: Concept(
            name=concept_name,
            slug=slugify(concept_name),
            subject=subject,
        )
        for concept_name in concept_names
    }


def _legacy_question(
    subject: Subject,
    question_data: dict,
    concepts: dict[str, Concept],
    concept_names: list[str],
) -> Question:
    question = Question(
        subject=subject,
        stem=question_data["stem"],
        difficulty=Difficulty.INTERMEDIATE,
        source_reference=question_data["source_reference"],
    )
    question.choices = [
        Choice(
            text=choice["text"],
            is_correct=choice.get("is_correct", False),
            order_index=choice_index,
        )
        for choice_index, choice in enumerate(question_data["choices"], start=1)
    ]
    question.concept_links = [
        QuestionConcept(concept=concepts[name], is_primary=index == 0)
        for index, name in enumerate(concept_names)
    ]
    return question
