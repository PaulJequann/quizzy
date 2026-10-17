from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, exp, sqrt
from typing import NamedTuple

from app.models import QuestionType

ALPHA0 = 1.0
BETA0 = 1.0
MAX_EFFECTIVE_WEIGHT_PER_FAMILY = 1.5
SUCCESS_FRICTION_WEIGHT = {1: 0.45, 2: 0.85, 3: 1.15, None: 1.0}
FAILURE_FRICTION_WEIGHT = {1: 1.25, 2: 0.90, 3: 0.80, None: 1.0}


class EvidenceWeight(NamedTuple):
    success: float
    failure: float


QUESTION_TYPE_WEIGHT = {
    QuestionType.MULTIPLE_CHOICE: EvidenceWeight(success=1.0, failure=1.0),
    QuestionType.OPEN_ENDED: EvidenceWeight(success=1.5, failure=1.0),
}


@dataclass(frozen=True)
class MasteryEvidenceInput:
    effective_success: float
    effective_failure: float
    primary_attempts: int
    secondary_attempts: int
    healthy_primary_question_count: int
    primary_seen_question_count: int
    unresolved_primary_miss_count: int
    suspect_evidence_ignored: bool
    due_at: datetime | None
    interval_days: int | None
    consecutive_spaced_correct: int


@dataclass(frozen=True)
class MasteryEvidenceSummary:
    mastery_state: str
    proficiency_band: str
    conservative_performance: float
    coverage_score: float
    confidence_score: float
    reason_codes: list[str]


@dataclass(frozen=True)
class MasteryScoreSnapshot:
    conservative_performance: float
    coverage_score: float
    confidence_score: float
    now: datetime


def summarize_mastery_evidence(
    data: MasteryEvidenceInput,
    *,
    now: datetime | None = None,
) -> MasteryEvidenceSummary:
    checked_at = now or datetime.now(UTC)
    conservative_performance = _conservative_performance(data)
    coverage_score = _coverage_score(data)
    confidence_score = _confidence_score(data, coverage_score)
    score = MasteryScoreSnapshot(
        conservative_performance=conservative_performance,
        coverage_score=coverage_score,
        confidence_score=confidence_score,
        now=checked_at,
    )
    mastery_state = _mastery_state(
        data,
        score,
    )
    reason_codes = _reason_codes(
        data,
        score,
    )
    return MasteryEvidenceSummary(
        mastery_state=mastery_state,
        proficiency_band=_proficiency_band(
            mastery_state=mastery_state,
            conservative_performance=conservative_performance,
            coverage_score=coverage_score,
            reason_codes=reason_codes,
        ),
        conservative_performance=conservative_performance,
        coverage_score=coverage_score,
        confidence_score=confidence_score,
        reason_codes=reason_codes,
    )


def answer_evidence_weight(
    question_type: QuestionType, score: float, friction_rating: int | None
) -> EvidenceWeight:
    type_weight = QUESTION_TYPE_WEIGHT[question_type]
    return EvidenceWeight(
        success=score * type_weight.success * SUCCESS_FRICTION_WEIGHT[friction_rating],
        failure=(1 - score) * type_weight.failure * FAILURE_FRICTION_WEIGHT[friction_rating],
    )


def capped_family_evidence(
    family_success_weight: dict[int, float],
    family_failure_weight: dict[int, float],
    family_question_type: dict[int, QuestionType],
) -> tuple[float, float]:
    effective_success = 0.0
    effective_failure = 0.0
    family_keys = set(family_success_weight) | set(family_failure_weight)
    for family_key in family_keys:
        success = family_success_weight.get(family_key, 0.0)
        failure = family_failure_weight.get(family_key, 0.0)
        cap_weight = QUESTION_TYPE_WEIGHT[family_question_type[family_key]].success
        scale = _family_scale(success + failure, cap_weight)
        effective_success += success * scale
        effective_failure += failure * scale
    return effective_success, effective_failure


def _family_scale(total: float, cap_weight: float) -> float:
    if not total:
        return 1.0
    return min(1.0, MAX_EFFECTIVE_WEIGHT_PER_FAMILY * cap_weight / total)


def _conservative_performance(data: MasteryEvidenceInput) -> float:
    alpha = ALPHA0 + data.effective_success
    beta = BETA0 + data.effective_failure
    posterior_mean = alpha / (alpha + beta)
    variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1))
    return max(0.0, posterior_mean - sqrt(variance))


def _coverage_score(data: MasteryEvidenceInput) -> float:
    if data.healthy_primary_question_count == 0:
        return 0.0
    target = min(
        data.healthy_primary_question_count,
        max(3, ceil(0.35 * data.healthy_primary_question_count)),
    )
    return min(1.0, data.primary_seen_question_count / target)


def _confidence_score(data: MasteryEvidenceInput, coverage_score: float) -> float:
    effective_attempt_count = data.effective_success + data.effective_failure
    evidence_confidence = 1.0 - exp(-effective_attempt_count / 6.0)
    return min(evidence_confidence, coverage_score)


def _mastery_state(
    data: MasteryEvidenceInput,
    score: MasteryScoreSnapshot,
) -> str:
    checks = [
        _unseen_state(data),
        _secondary_only_state(data),
        _unresolved_miss_state(data),
        _low_performance_state(score.conservative_performance),
        _review_due_state(data, score.now),
        _mastered_state(data, score),
        _stable_state(score.conservative_performance, score.coverage_score),
    ]
    return next((state for state in checks if state is not None), "learning")


def _reason_codes(
    data: MasteryEvidenceInput,
    score: MasteryScoreSnapshot,
) -> list[str]:
    rules = [
        (_is_unseen(data), "unseen"),
        (_has_only_secondary_evidence(data), "only_secondary_evidence"),
        (data.unresolved_primary_miss_count > 0, "unresolved_primary_miss"),
        (score.conservative_performance < 0.70, "low_performance"),
        (score.coverage_score < 0.80, "low_coverage"),
        (score.confidence_score < 0.70, "low_confidence"),
        (data.healthy_primary_question_count == 0, "too_few_healthy_questions"),
        (data.suspect_evidence_ignored, "suspect_question_evidence_ignored"),
        (_is_review_due(data, score.now), "review_due"),
        (data.consecutive_spaced_correct < 3, "needs_fluent_spaced_retrieval"),
    ]
    return [reason for matched, reason in rules if matched]


def _proficiency_band(
    *,
    mastery_state: str,
    conservative_performance: float,
    coverage_score: float,
    reason_codes: list[str],
) -> str:
    if mastery_state == "unseen":
        return "weak"
    if mastery_state == "learning" and "only_secondary_evidence" in reason_codes:
        return "weak"
    if conservative_performance >= 0.84 and coverage_score >= 0.70:
        return "strong"
    if conservative_performance >= 0.70:
        return "improving"
    return "weak"


def _unseen_state(data: MasteryEvidenceInput) -> str | None:
    return "unseen" if _is_unseen(data) else None


def _secondary_only_state(data: MasteryEvidenceInput) -> str | None:
    return "learning" if _has_only_secondary_evidence(data) else None


def _unresolved_miss_state(data: MasteryEvidenceInput) -> str | None:
    return "learning" if data.unresolved_primary_miss_count > 0 else None


def _low_performance_state(conservative_performance: float) -> str | None:
    return "learning" if conservative_performance < 0.70 else None


def _review_due_state(data: MasteryEvidenceInput, now: datetime) -> str | None:
    return "review_due" if _is_review_due(data, now) else None


def _mastered_state(
    data: MasteryEvidenceInput,
    score: MasteryScoreSnapshot,
) -> str | None:
    mastered = (
        score.conservative_performance >= 0.84
        and score.coverage_score >= 0.80
        and score.confidence_score >= 0.70
        and data.consecutive_spaced_correct >= 3
        and (data.interval_days or 0) >= 30
    )
    return "mastered" if mastered else None


def _stable_state(conservative_performance: float, coverage_score: float) -> str | None:
    return "stable" if conservative_performance >= 0.75 and coverage_score >= 0.50 else None


def _is_unseen(data: MasteryEvidenceInput) -> bool:
    return data.primary_attempts == 0 and data.secondary_attempts == 0


def _has_only_secondary_evidence(data: MasteryEvidenceInput) -> bool:
    return data.primary_attempts == 0 and data.secondary_attempts > 0


def _is_review_due(data: MasteryEvidenceInput, now: datetime) -> bool:
    return data.due_at is not None and now >= data.due_at
