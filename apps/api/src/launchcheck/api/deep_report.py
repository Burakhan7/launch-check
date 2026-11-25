"""Faz 2 rapor bölümleri (PHASE2 §8). Hepsi deterministik; LLM kullanılmaz.

- phase_diff (§8.1): Faz 1 ve Faz 2 sayıları ve skorları yan yana
- verification (§8.5): Faz 1 bulgularının doğrulama etiketleri
- plan (§8.8): bugün / bu hafta / bu ay (önem + etki kartındaki tahmini düzeltme süresi)
- gates (§8.9): test edilmemiş kapılar (`meta.gates`, orkestratör üretir)
- rescan (§8.10): önceki Faz 2 taramasıyla fark (parmak iziyle eşleştirme)
- assets: ekran görüntüleri (tür, sayfa, boyut; dosyanın kendisi /assets uç noktasından)
"""

from typing import Any

from launchcheck.api.schemas import FindingOut
from launchcheck.checks.base import Severity
from launchcheck.db.models import FindingRow, Scan, ScanAsset, Verification

TODAY_MAX_MIN = 30
WEEK_MAX_MIN = 120
# Etki kartı olmayan bulgular için kaba süre tahmini (önem → dakika)
DEFAULT_MINUTES = {
    Severity.CRITICAL: 30,
    Severity.HIGH: 60,
    Severity.MEDIUM: 60,
    Severity.LOW: 30,
    Severity.INFO: 15,
}
URGENT = (Severity.CRITICAL, Severity.HIGH)


def _minutes(f: FindingOut) -> int:
    return f.impact.fix_minutes if f.impact else DEFAULT_MINUTES[f.severity]


def priority_plan(findings: list[FindingOut]) -> dict[str, list[str]]:
    """Bugün: critical/high ve ≤ 30 dk. Bu hafta: kalan critical/high + ≤ 120 dk medium.
    Bu ay: geri kalanlar. Yanlış alarm ve bilgi bulguları plana girmez."""
    plan: dict[str, list[str]] = {"today": [], "week": [], "month": []}
    for f in findings:
        if f.id is None or f.severity == Severity.INFO or f.verification == Verification.REFUTED:
            continue
        minutes = _minutes(f)
        if f.severity in URGENT and minutes <= TODAY_MAX_MIN:
            group = "today"
        elif f.severity in URGENT or (f.severity == Severity.MEDIUM and minutes <= WEEK_MAX_MIN):
            group = "week"
        else:
            group = "month"
        plan[group].append(str(f.id))
    return plan


def _verification(rows: list[FindingRow]) -> dict[str, int]:
    counts = {v.value: 0 for v in Verification}
    for r in rows:
        if r.source_phase == 1:
            counts[(r.verification or Verification.NOT_VERIFIABLE).value] += 1
    return counts


def phase_diff(
    scan: Scan, parent: Scan | None, rows: list[FindingRow], gates_total: int
) -> dict[str, Any]:
    phase1 = [r for r in rows if r.source_phase == 1]
    new = [r for r in rows if r.source_phase == 2 and r.severity != Severity.INFO]
    return {
        "phase1": {
            "findings": len(phase1),
            "score": parent.score if parent is not None else None,
        },
        "phase2": {
            "new_findings": len(new),
            "verification": _verification(rows),
            "forgotten_subdomains": sum(
                1 for r in rows if r.check_id == "web.external.forgotten_subdomain"
            ),
            "untested_gates": gates_total,
            "score": scan.score,
        },
        # Faz 2 skoru düşük çıkabilir: bu doğrudur, rapor açıkça söyler (§8.1)
        "score_dropped": parent is not None
        and parent.score is not None
        and scan.score is not None
        and scan.score < parent.score,
    }


def rescan_diff(
    current: list[FindingOut], previous: list[FindingOut], scan: Scan, before: Scan | None
) -> dict[str, Any] | None:
    if before is None:
        return None

    def keyed(items: list[FindingOut]) -> dict[str, FindingOut]:
        out: dict[str, FindingOut] = {}
        for f in items:
            if f.verification != Verification.REFUTED and f.severity != Severity.INFO:
                out.setdefault(f.fingerprint, f)
        return out

    now, then = keyed(current), keyed(previous)

    def item(f: FindingOut) -> dict[str, str]:
        return {"check_id": f.check_id, "title": f.title, "severity": f.severity.value}

    return {
        "compare_to": str(before.id),
        "score_before": before.score,
        "score_after": scan.score,
        "closed": [item(f) for k, f in then.items() if k not in now],
        "new": [item(f) for k, f in now.items() if k not in then],
        "persisting": [item(f) for k, f in now.items() if k in then],
    }


def asset_list(assets: list[ScanAsset]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(a.id),
            "kind": a.kind,
            "page_url": a.page_url,
            "width": a.width,
            "height": a.height,
        }
        for a in sorted(assets, key=lambda a: (a.kind, a.page_url))
    ]


def deep_sections(
    scan: Scan,
    parent: Scan | None,
    rows: list[FindingRow],
    findings: list[FindingOut],
    assets: list[ScanAsset],
    previous: list[FindingOut] | None = None,
    before: Scan | None = None,
) -> dict[str, Any]:
    gates = (scan.meta or {}).get("gates") or {}
    return {
        "phase_diff": phase_diff(scan, parent, rows, int(gates.get("total") or 0)),
        "plan": priority_plan(findings),
        "gates": gates,
        "rescan": rescan_diff(findings, previous or [], scan, before),
        "assets": asset_list(assets),
    }
