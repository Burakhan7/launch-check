"""DB kayıtlarından dil bazlı rapor üretimi."""

from datetime import timedelta

from launchcheck.api.schemas import ErrorInfo, FindingOut, PassedCheckOut, ScanReport
from launchcheck.checks.registry import Registry
from launchcheck.db.models import FindingRow, Scan, ScanStatus
from launchcheck.i18n import error_message, finding_text
from launchcheck.scoring import label_for

INTERNAL_META_KEYS = ("checks_failed",)


def _finding_out(row: FindingRow, lang: str) -> FindingOut:
    text = finding_text(row.check_id, lang)
    return FindingOut(
        check_id=row.check_id,
        severity=row.severity,
        category=row.category,
        title=text.title,
        description=text.description,
        why_it_matters=text.why_it_matters,
        fix_steps=text.fix_steps,
        fix_prompt=text.fix_prompt,
        evidence=row.evidence or {},
    )


def _passed(
    scan: Scan, rows: list[FindingRow], registry: Registry, lang: str
) -> list[PassedCheckOut]:
    """Bulgu üretmeyen ve hata vermeyen check'ler: kullanıcıya neyi kontrol ettiğimizi gösterir."""
    if scan.status != ScanStatus.DONE:
        return []
    flagged = {r.check_id for r in rows}
    failed = set((scan.meta or {}).get("checks_failed", []))
    return [
        PassedCheckOut(check_id=c.id, category=c.category, title=finding_text(c.id, lang).title)
        for c in registry.for_target(scan.target_type, scan.phase)
        if c.id not in flagged and c.id not in failed
    ]


def build_report(
    scan: Scan, rows: list[FindingRow], registry: Registry, lang: str, retention_days: int
) -> ScanReport:
    ordered = sorted(rows, key=lambda r: (r.severity.rank, r.check_id))
    meta = {k: v for k, v in (scan.meta or {}).items() if k not in INTERNAL_META_KEYS}
    error = (
        ErrorInfo(code=scan.error_code, message=error_message(scan.error_code, lang))
        if scan.error_code
        else None
    )
    return ScanReport(
        scan_id=scan.id,
        status=scan.status,
        target_type=scan.target_type,
        target_label=scan.target_label,
        phase=scan.phase,
        score=scan.score,
        score_label=label_for(scan.score) if scan.score is not None else None,
        category_scores=scan.category_scores,
        meta=meta,
        error=error,
        findings=[_finding_out(r, lang) for r in ordered],
        passed=_passed(scan, rows, registry, lang),
        created_at=scan.created_at,
        finished_at=scan.finished_at,
        expires_at=scan.created_at + timedelta(days=retention_days),
    )
