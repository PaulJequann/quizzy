from __future__ import annotations

from typing import Any

from app.services import concept_service
from app.services.generation_service import GeneratedQuestionInput

_MIN_QUESTIONS = 3
_MIN_QUESTION_MATCHES = 3
_MIN_TOPIC_TOKENS = 2
_MAX_TOPIC_TOKENS = 5


def uncovered_source_topic(
    questions: list[GeneratedQuestionInput],
    *,
    plan: dict[str, Any],
    subject_name: str,
    source_titles: list[str] | None = None,
) -> str | None:
    if len(questions) < _MIN_QUESTIONS:
        return None
    return _uncovered_source_topic(
        questions,
        proposed_names=[entry["name"] for entry in plan["entries"]],
        subject_name=subject_name,
        source_titles=source_titles or [],
    )


def _uncovered_source_topic(
    questions: list[GeneratedQuestionInput],
    *,
    proposed_names: list[str],
    subject_name: str,
    source_titles: list[str],
) -> str | None:
    covered_tokens = _covered_token_sets(proposed_names, subject_name=subject_name)
    candidates = _source_topic_candidates(source_titles, subject_name=subject_name)
    uncovered = [
        candidate
        for candidate in candidates
        if not _topic_is_covered(candidate, covered_tokens)
        and _question_match_count(candidate, questions, subject_name=subject_name)
        >= _MIN_QUESTION_MATCHES
    ]
    if not uncovered:
        return None
    return _topic_to_display(max(uncovered, key=lambda candidate: len(candidate)))


def _source_topic_candidates(
    source_titles: list[str], *, subject_name: str
) -> set[tuple[str, ...]]:
    candidates: set[tuple[str, ...]] = set()
    for title in source_titles:
        tokens = _normalized_tokens(title, subject_name=subject_name)
        max_size = min(len(tokens), _MAX_TOPIC_TOKENS)
        for size in range(_MIN_TOPIC_TOKENS, max_size + 1):
            candidates.update(
                tuple(tokens[index : index + size]) for index in range(0, len(tokens) - size + 1)
            )
    return candidates


def _question_match_count(
    candidate: tuple[str, ...],
    questions: list[GeneratedQuestionInput],
    *,
    subject_name: str,
) -> int:
    candidate_tokens = set(candidate)
    return sum(
        candidate_tokens
        <= set(_normalized_tokens(_question_text(question), subject_name=subject_name))
        for question in questions
    )


def _question_text(question: GeneratedQuestionInput) -> str:
    return " ".join(
        value
        for value in [
            question.stem,
            question.explanation or "",
            question.source_excerpt or "",
        ]
        if value
    )


def _covered_token_sets(proposed_names: list[str], *, subject_name: str) -> list[set[str]]:
    return [set(_normalized_tokens(name, subject_name=subject_name)) for name in proposed_names]


def _topic_is_covered(topic: tuple[str, ...], covered_tokens: list[set[str]]) -> bool:
    topic_tokens = set(topic)
    return any(topic_tokens <= tokens for tokens in covered_tokens)


def _normalized_tokens(text: str, *, subject_name: str) -> list[str]:
    normalized = concept_service.normalize_concept_name(text, subject_name=subject_name)
    return [token for token in normalized.split("-") if token]


def _topic_to_display(topic: tuple[str, ...]) -> str:
    return " ".join(topic).title()
