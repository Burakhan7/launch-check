"""DB kayıtlarından dil bazlı rapor üretimi."""

from datetime import datetime, timedelta

from launchcheck.api.deep_queue import QueueInfo
from launchcheck.api.deep_report import deep_sections
from launchcheck.api.schemas import (
    ErrorInfo,
    FindingOut,
    ImpactOut,
    PassedCheckOut,
    ScanReport,
    TimelineItem,
)
from launchcheck.checks.base import Severity
from launchcheck.checks.registry import Registry
from launchcheck.db.models import FindingRow, Scan, ScanAsset, ScanStatus
from launchcheck.i18n import (
    error_message,
    finding_text,
    fragment_fn,
    impact_text,
    passed_title,
)
from launchcheck.scoring import label_for

INTERNAL_META_KEYS = ("checks_failed",)
# Yeniden adlandırılan check'ler: eski taramalar (en fazla saklama süresi kadar) yeni metinle
# gösterilir.
RENAMED_CHECKS = {"web.safety.live_chat_without_age_gate": "web.safety.age_gate_missing"}


def canonical_check_id(check_id: str) -> str:
    return RENAMED_CHECKS.get(check_id, check_id)


def _text_params(row: FindingRow, registry: Registry, lang: str) -> dict[str, str]:
    """Dinamik metinler (ör. "Ana sayfanda og:image eksik") kanıttan üretilir."""
    check_id = canonical_check_id(row.check_id)
    check = registry.get(check_id)
    if check is None:
        return {}
    try:
        return check.text_params(row.evidence or {}, fragment_fn(check_id, lang))
    except Exception:  # eski/bozuk kanıt raporu bozmasın; şablon olduğu gibi kalır
        return {}


IMPACT_MIN_SEVERITY = Severity.MEDIUM


def _impact(check_id: str, row: FindingRow, lang: str, params: dict[str, str]) -> ImpactOut | None:
    """Etki kartı sadece önemi medium ve üstü bulgularda (PHASE2 §8.4)."""
    if row.severity.rank > IMPACT_MIN_SEVERITY.rank:
        return None
    impact = impact_text(check_id, lang, params)
    if impact is None:
        return None
    return ImpactOut(
        who=impact.who,
        ease=impact.ease,
        consequence=impact.consequence,
        legal=impact.legal,
        timeline=[TimelineItem(**item) for item in impact.timeline],
        fix_minutes=impact.fix_minutes,
    )


def _fingerprint(check_id: str, row: FindingRow, registry: Registry) -> str:
    check = registry.get(check_id)
    try:
        return check.fingerprint(row.evidence or {}) if check else check_id
    except Exception:
        return check_id


def _finding_out(
    row: FindingRow, registry: Registry, lang: str, with_impact: bool = False
) -> FindingOut:
    check_id = canonical_check_id(row.check_id)
    params = _text_params(row, registry, lang)
    text = finding_text(check_id, lang, params)
    return FindingOut(
        id=row.id,
        check_id=check_id,
        severity=row.severity,
        category=row.category,
        title=text.title,
        description=text.description,
        why_it_matters=text.why_it_matters,
        fix_steps=text.fix_steps,
        fix_prompt=text.fix_prompt,
        evidence=row.evidence or {},
        source_phase=row.source_phase or 1,
        verification=row.verification,
        module=row.module,
        impact=_impact(check_id, row, lang, params) if with_impact else None,
        fingerprint=_fingerprint(check_id, row, registry),
    )


def _completed_modules(scan: Scan) -> set[str]:
    modules = ((scan.progress or {}).get("modules") or {}).items()
    return {name for name, info in modules if (info or {}).get("status") == "done"}


def _passed(
    scan: Scan, rows: list[FindingRow], registry: Registry, lang: str
) -> list[PassedCheckOut]:
    """Bulgu üretmeyen ve hata vermeyen check'ler: kullanıcıya neyi kontrol ettiğimizi gösterir.
    Faz 2 check'leri sadece modülleri tamamen çalıştıysa "geçti" sayılır."""
    if scan.status != ScanStatus.DONE:
        return []
    flagged = {canonical_check_id(r.check_id) for r in rows}
    meta = scan.meta or {}
    # Atlananlar: verisi olmadığı için değerlendirilemeyenler (ör. saha verisi yokken INP)
    failed = set(meta.get("checks_failed", [])) | set(meta.get("checks_skipped", []))
    completed = _completed_modules(scan)
    return [
        PassedCheckOut(check_id=c.id, category=c.category, title=passed_title(c.id, lang))
        for c in registry.for_target(scan.target_type, scan.phase)
        if c.id not in flagged
        and c.id not in failed
        and (c.phase == 1 or (c.module is not None and c.module in completed))
    ]


def _expires_at(scan: Scan, parent: Scan | None, retention_days: int) -> datetime:
    """Faz 2 raporu, dayandığı Faz 1 raporu silinince onunla birlikte silinir."""
    own = scan.created_at + timedelta(days=retention_days)
    if parent is None:
        return own
    return min(own, parent.created_at + timedelta(days=retention_days))


def build_report(
    scan: Scan,
    rows: list[FindingRow],
    registry: Registry,
    lang: str,
    retention_days: int,
    parent: Scan | None = None,
    queue: QueueInfo | None = None,
    assets: list[ScanAsset] | None = None,
    previous: tuple[Scan, list[FindingRow]] | None = None,
) -> ScanReport:
    ordered = sorted(rows, key=lambda r: (r.severity.rank, r.check_id))
    deep = scan.phase == 2
    findings = [_finding_out(r, registry, lang, with_impact=deep) for r in ordered]
    sections = None
    if deep and scan.status == ScanStatus.DONE:
        before, before_rows = previous if previous else (None, [])
        sections = deep_sections(
            scan,
            parent,
            rows,
            findings,
            assets or [],
            [_finding_out(r, registry, lang) for r in before_rows],
            before,
        )
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
        findings=findings,
        passed=_passed(scan, rows, registry, lang),
        created_at=scan.created_at,
        finished_at=scan.finished_at,
        expires_at=_expires_at(scan, parent, retention_days),
        parent_scan_id=scan.parent_scan_id,
        progress=scan.progress,
        queue_position=queue.position if queue else None,
        eta_s=queue.eta_s if queue else None,
        sections=sections,
    )
