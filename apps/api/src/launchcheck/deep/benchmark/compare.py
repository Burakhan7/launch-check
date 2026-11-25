"""Deterministik karşılaştırma (PHASE2 §7.4): fark matrisi, ortak desenler, önde olunan yerler,
eksik sayfa türleri, hız. LLM kullanılmaz; sayılar sadece anatomilerden gelir.

Bilinmeyen değer (None; ör. örnekte kayıt formu bulunamadı) paydaya girmez. Kullanıcının değeri
bilinmiyorsa o desen için "sende yok" denmez.
"""

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PATTERN_SHARE = 0.6  # spec: örneklerin en az %60'ı
STRONG_SHARE = 0.8  # ≥ %80 → medium
MIN_EXAMPLES = 2
MAX_PATTERNS = 8
SLOWER_FACTOR = 1.5
EARLY_SECTIONS = 3
REBUILD_SHARE = 0.5

Anatomy = dict[str, Any]
Feature = Callable[[Anatomy], bool | None]


def _get(a: Anatomy, *path: str) -> Any:
    value: Any = a
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _signup(key: str, fn: Callable[[dict[str, Any]], bool]) -> Feature:
    def feature(a: Anatomy) -> bool | None:
        signup = a.get("signup")
        return None if not isinstance(signup, dict) else fn(signup)

    feature.__name__ = key
    return feature


def _early_proof(a: Anatomy) -> bool:
    early = (a.get("sections") or [])[:EARLY_SECTIONS]
    return "testimonials" in early or "logos" in early


def _price_one_click(a: Anatomy) -> bool:
    clicks = _get(a, "pricing", "clicks_to_price")
    return clicks is not None and clicks <= 1


def _priced(key: str) -> Feature:
    """Fiyat sayfası özellikleri sadece fiyatı olan sitelerde anlamlı."""

    def feature(a: Anatomy) -> bool | None:
        if not _get(a, "pricing", "plans"):
            return None
        return bool(_get(a, "pricing", key))

    return feature


# Desen anahtarı → (özellik fonksiyonu). Anahtarlar i18n `fragments.pattern_<anahtar>` olur.
FEATURES: dict[str, Feature] = {
    "social_proof_early": _early_proof,
    "testimonials": lambda a: bool(_get(a, "trust", "testimonials")),
    "logo_band": lambda a: bool(_get(a, "trust", "logo_band")),
    "user_count_claim": lambda a: bool(_get(a, "trust", "user_count_claim")),
    "security_badges": lambda a: bool(_get(a, "trust", "security_badges")),
    "case_studies": lambda a: bool(_get(a, "trust", "case_studies")),
    "contact": lambda a: bool(_get(a, "trust", "contact")),
    "company_info": lambda a: bool(_get(a, "trust", "company_info")),
    "hero_subheadline": lambda a: bool(_get(a, "hero", "subheadline")),
    "hero_cta": lambda a: (_get(a, "hero", "cta_count_above_fold") or 0) >= 1,
    "hero_media": lambda a: _get(a, "hero", "media_type") not in (None, "none"),
    "faq_section": lambda a: "faq" in (a.get("sections") or []),
    "how_it_works": lambda a: "how_it_works" in (a.get("sections") or []),
    "final_cta": lambda a: "final_cta" in (a.get("sections") or []),
    "price_one_click": _price_one_click,
    "free_tier": _priced("free_tier"),
    "annual_discount": _priced("annual_discount"),
    "highlighted_plan": _priced("highlighted_plan"),
    "social_login": _signup("social_login", lambda s: bool(s.get("social_login"))),
    "short_signup": _signup("short_signup", lambda s: int(s.get("fields") or 0) <= 3),
    "no_card_signup": _signup("no_card_signup", lambda s: not s.get("card_required")),
    "og_image": lambda a: bool(_get(a, "share", "og_image")),
}


@dataclass
class Comparison:
    examples: int
    matrix: list[dict[str, Any]] = field(default_factory=list)
    patterns: list[dict[str, Any]] = field(default_factory=list)  # sende eksik ortak desenler
    ahead: list[dict[str, Any]] = field(default_factory=list)
    missing_pages: list[dict[str, Any]] = field(default_factory=list)
    speed: dict[str, Any] | None = None  # sen vs ortanca (aynı yöntem)
    rebuild_sections: list[str] = field(default_factory=list)  # ortak bölüm sırası


def _feature_row(key: str, fn: Feature, user: Anatomy, examples: list[Anatomy]) -> dict[str, Any]:
    values = [fn(a) for a in examples]
    known = [v for v in values if v is not None]
    have = sum(1 for v in known if v)
    return {
        "key": key,
        "user": fn(user),
        "examples": values,
        "have": have,
        "known": len(known),
        "share": round(have / len(known), 2) if known else None,
    }


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def speed(user: Anatomy, examples: list[Anatomy]) -> dict[str, Any] | None:
    """Saha verisi (CrUX) sende ve en az 2 örnekte varsa o, yoksa aynı tarayıcıyla ölçülmüş
    laboratuvar LCP'si. İki yöntem karıştırılmaz."""
    for key, method in (("field_lcp_ms", "field"), ("lcp_ms", "lab")):
        mine = _get(user, "perf", key)
        peers = [v for a in examples if (v := _get(a, "perf", key)) is not None]
        if mine is not None and len(peers) >= MIN_EXAMPLES:
            median = _median([float(p) for p in peers])
            return {"method": method, "user_ms": mine, "median_ms": median, "peers": len(peers)}
    return None


def _ahead(
    rows: list[dict[str, Any]], user: Anatomy, examples: list[Anatomy]
) -> list[dict[str, Any]]:
    out = [
        {"key": r["key"], "share": r["share"]}
        for r in rows
        if r["user"] and r["share"] is not None and r["known"] >= MIN_EXAMPLES and r["share"] < 0.4
    ]
    fields = [int(s["fields"]) for a in examples if isinstance((s := a.get("signup")), dict)]
    mine = user.get("signup")
    if isinstance(mine, dict) and len(fields) >= MIN_EXAMPLES:
        median = _median([float(f) for f in fields])
        if median is not None and int(mine["fields"]) < median:
            out.append({"key": "fewer_signup_fields", "user": mine["fields"], "median": median})
    s = speed(user, examples)
    if s and s["median_ms"] and s["user_ms"] < 0.8 * s["median_ms"]:
        out.append({"key": "faster", "user": s["user_ms"], "median": s["median_ms"]})
    return out


def _missing_pages(user: Anatomy, examples: list[Anatomy]) -> list[dict[str, Any]]:
    mine = set(user.get("page_inventory") or [])
    counts: dict[str, int] = {}
    for a in examples:
        for kind in set(a.get("page_inventory") or []):
            counts[kind] = counts.get(kind, 0) + 1
    total = len(examples)
    return sorted(
        (
            {"type": kind, "have": n, "total": total}
            for kind, n in counts.items()
            if kind not in mine and n / total >= PATTERN_SHARE and n >= MIN_EXAMPLES
        ),
        key=lambda x: -x["have"],
    )


def rebuild_sections(examples: list[Anatomy]) -> list[str]:
    """Örneklerin en az yarısında olan bölümler, ortalama sıralarına göre (hero hep ilk)."""
    positions: dict[str, list[float]] = {}
    for a in examples:
        sections = a.get("sections") or []
        for i, kind in enumerate(sections):
            positions.setdefault(kind, []).append(i / max(1, len(sections) - 1))
    common = [
        (kind, statistics.mean(pos))
        for kind, pos in positions.items()
        if len(pos) / len(examples) >= REBUILD_SHARE and kind != "hero"
    ]
    return ["hero", *[k for k, _ in sorted(common, key=lambda kv: kv[1])]]


def compare(user: Anatomy, examples: list[Anatomy]) -> Comparison:
    result = Comparison(examples=len(examples))
    if len(examples) < MIN_EXAMPLES:
        return result
    rows = [_feature_row(k, fn, user, examples) for k, fn in FEATURES.items()]
    result.matrix = rows
    result.patterns = sorted(
        (
            {"key": r["key"], "have": r["have"], "total": r["known"], "share": r["share"]}
            for r in rows
            if r["user"] is False
            and r["known"] >= MIN_EXAMPLES
            and r["share"] is not None
            and r["share"] >= PATTERN_SHARE
        ),
        key=lambda p: -p["share"],
    )[:MAX_PATTERNS]
    result.ahead = _ahead(rows, user, examples)
    result.missing_pages = _missing_pages(user, examples)
    result.speed = speed(user, examples)
    result.rebuild_sections = rebuild_sections(examples)
    return result
