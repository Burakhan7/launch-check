from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.scoring import ScoreLabel, compute_score, label_for

W = {"security": 0.7, "prod_readiness": 0.3}


def f(check_id: str, sev: Severity, cat: Category = Category.SECURITY) -> Finding:
    return Finding(check_id, sev, cat)


def test_no_findings_is_perfect() -> None:
    r = compute_score([], W)
    assert r.overall == 100
    assert r.categories == {"security": 100, "prod_readiness": 100}
    assert r.label == ScoreLabel.GOOD


def test_critical_caps_overall_at_49() -> None:
    r = compute_score([f("web.a.b", Severity.CRITICAL)], W)
    assert r.categories["security"] == 70
    # ağırlıklı: 0.7*70 + 0.3*100 = 79 → critical tavanı 49
    assert r.overall == 49
    assert r.label == ScoreLabel.NOT_READY


def test_repeated_findings_half_penalty_and_cap() -> None:
    # high=12: 12 + 6 = 18
    assert compute_score([f("web.a.b", Severity.HIGH)] * 2, W).categories["security"] == 82
    # 12 + 6*4 = 36 → en fazla 2×12 = 24
    assert compute_score([f("web.a.b", Severity.HIGH)] * 5, W).categories["security"] == 76


def test_different_checks_full_penalty_each() -> None:
    r = compute_score([f("web.a.b", Severity.HIGH), f("web.a.c", Severity.HIGH)], W)
    assert r.categories["security"] == 76


def test_category_floor_zero() -> None:
    findings = [f(f"web.a.c{i}", Severity.CRITICAL) for i in range(5)]
    assert compute_score(findings, W).categories["security"] == 0


def test_weighted_average_and_rounding() -> None:
    r = compute_score([f("web.a.b", Severity.MEDIUM, Category.PROD_READINESS)], W)
    assert r.categories["prod_readiness"] == 95
    # 0.7*100 + 0.3*95 = 98.5 → 99 (yarım yukarı; Python round() 98 verirdi)
    assert r.overall == 99


def test_info_has_no_penalty() -> None:
    assert compute_score([f("web.a.b", Severity.INFO)], W).overall == 100


def test_unweighted_category_shown_but_not_counted() -> None:
    r = compute_score([f("apk.a.b", Severity.HIGH, Category.STORE_COMPLIANCE)], W)
    assert r.categories["store_compliance"] == 88
    assert r.overall == 100


def test_labels() -> None:
    assert label_for(0) == label_for(49) == ScoreLabel.NOT_READY
    assert label_for(50) == label_for(79) == ScoreLabel.NEEDS_WORK
    assert label_for(80) == label_for(100) == ScoreLabel.GOOD
