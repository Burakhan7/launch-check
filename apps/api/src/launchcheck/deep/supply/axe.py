"""axe-core (MPL-2.0, değiştirilmeden kullanılır) ile erişilebilirlik ölçümü.

axe kaynağı `page.evaluate` ile çalıştırılır (CDP üzerinden): sayfanın CSP'si `<script>` eklemeyi
engellese bile ölçüm yapılır, sayfaya kalıcı bir şey eklenmez. Sadece WCAG 2.x A/AA kuralları;
"best-practice" kuralları gürültü üretmesin diye çalıştırılmaz. Raporlanan: `serious`/`critical`.
"""

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from launchcheck.deep.privacy_data import AxeViolation

REPORTED_IMPACTS = ("critical", "serious")
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"]
MAX_SOURCE_BYTES = 4 * 1024 * 1024
RUN_JS = """async (opts) => {
  const result = await axe.run(document, opts);
  return result.violations.map((v) => ({
    id: v.id, impact: v.impact, help: (v.help || '').slice(0, 200), helpUrl: v.helpUrl || '',
    count: v.nodes.length,
    targets: v.nodes.slice(0, 3).map((n) => (n.target || []).join(' ').slice(0, 120)),
  }));
}"""


@lru_cache(maxsize=2)
def load_axe_source(path: str) -> str | None:
    file = Path(path) if path else None
    if file is None or not file.is_file() or file.stat().st_size > MAX_SOURCE_BYTES:
        return None
    return file.read_text("utf-8")


def run_options() -> dict[str, Any]:
    return {"resultTypes": ["violations"], "runOnly": {"type": "tag", "values": WCAG_TAGS}}


def group_violations(
    runs: Iterable[tuple[str, str, list[dict[str, Any]]]], limit: int = 15
) -> list[AxeViolation]:
    """(sayfa yolu, görünüm, axe ihlalleri) → kural bazında gruplu, önem sırasına göre."""
    grouped: dict[str, dict[str, Any]] = {}
    for page, viewport, violations in runs:
        for item in violations:
            impact = str(item.get("impact") or "")
            if impact not in REPORTED_IMPACTS:
                continue
            rule = str(item.get("id") or "")
            entry = grouped.setdefault(
                rule,
                {
                    "impact": impact,
                    "help": str(item.get("help") or ""),
                    "help_url": str(item.get("helpUrl") or ""),
                    "count": 0,
                    "pages": [],
                    "viewports": [],
                    "targets": [],
                },
            )
            if impact == "critical":
                entry["impact"] = "critical"
            entry["count"] += int(item.get("count") or 0)
            if page not in entry["pages"]:
                entry["pages"].append(page)
            if viewport not in entry["viewports"]:
                entry["viewports"].append(viewport)
            for target in item.get("targets") or []:
                if len(entry["targets"]) < 3 and target not in entry["targets"]:
                    entry["targets"].append(str(target))
    out = [
        AxeViolation(
            rule=rule,
            impact=e["impact"],
            help=e["help"],
            help_url=e["help_url"] if e["help_url"].startswith("https://") else "",
            count=e["count"],
            pages=tuple(e["pages"][:5]),
            viewports=tuple(e["viewports"]),
            targets=tuple(e["targets"]),
        )
        for rule, e in grouped.items()
    ]
    out.sort(key=lambda v: (v.impact != "critical", -v.count))
    return out[:limit]
