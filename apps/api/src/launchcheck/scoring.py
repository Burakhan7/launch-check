"""Skorlama (AGENTS.md §12)."""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from launchcheck.checks.base import Category, Finding, Severity

MAX_SCORE = 100
MIN_SCORE = 0
PENALTY: Mapping[Severity, float] = {
    Severity.CRITICAL: 30,
    Severity.HIGH: 12,
    Severity.MEDIUM: 5,
    Severity.LOW: 2,
    Severity.INFO: 0,
}
REPEAT_PENALTY_FACTOR = 0.5  # aynı check'ten sonraki bulgular yarım ceza
MAX_CHECK_PENALTY_MULTIPLIER = 2  # bir check'in toplam cezası en fazla 2× tam ceza
CRITICAL_SCORE_CAP = 49  # en az bir critical varsa genel skor bunu geçemez
NOT_READY_MAX = 49
NEEDS_WORK_MAX = 79


class ScoreLabel(StrEnum):
    NOT_READY = "not_ready"  # 0-49 "Yayına hazır değil"
    NEEDS_WORK = "needs_work"  # 50-79 "Düzeltilmesi gerekenler var"
    GOOD = "good"  # 80-100 "İyi durumda"


@dataclass(frozen=True)
class ScoreResult:
    overall: int
    categories: dict[str, int]
    label: ScoreLabel


def _round(value: float) -> int:
    return math.floor(value + 0.5)


def _check_penalty(findings: list[Finding]) -> float:
    penalties = sorted((PENALTY[f.severity] for f in findings), reverse=True)
    first = penalties[0]
    total = first + sum(p * REPEAT_PENALTY_FACTOR for p in penalties[1:])
    return min(total, first * MAX_CHECK_PENALTY_MULTIPLIER)


def label_for(score: int) -> ScoreLabel:
    if score <= NOT_READY_MAX:
        return ScoreLabel.NOT_READY
    if score <= NEEDS_WORK_MAX:
        return ScoreLabel.NEEDS_WORK
    return ScoreLabel.GOOD


def compute_score(findings: Iterable[Finding], weights: Mapping[str, float]) -> ScoreResult:
    """`weights` içindeki kategoriler her zaman skorlanır; ağırlığı olmayan ama bulgusu olan
    kategoriler de gösterilir ama genel skoru etkilemez."""
    findings = list(findings)
    by_check: dict[tuple[Category, str], list[Finding]] = defaultdict(list)
    for f in findings:
        by_check[(f.category, f.check_id)].append(f)

    penalty_by_category: dict[str, float] = defaultdict(float)
    for (category, _), group in by_check.items():
        penalty_by_category[category] += _check_penalty(group)

    category_names = list(weights) + [c for c in penalty_by_category if c not in weights]
    categories = {
        name: max(MIN_SCORE, _round(MAX_SCORE - penalty_by_category.get(name, 0.0)))
        for name in category_names
    }

    total_weight = sum(weights.values())
    if total_weight > 0:
        overall = _round(sum(categories[n] * w for n, w in weights.items()) / total_weight)
    else:
        overall = MAX_SCORE
    if any(f.severity == Severity.CRITICAL for f in findings):
        overall = min(overall, CRITICAL_SCORE_CAP)

    return ScoreResult(overall=overall, categories=categories, label=label_for(overall))
