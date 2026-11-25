"""Anatomi çıkarımı (PHASE2 §7.3): kullanıcının sitesi ve örnekler için AYNI fonksiyon, aynı şema.

Girdi: `anatomy.js` (ana sayfa yapısı, performans), `funnel.js` incelemeleri (fiyat ve kayıt
sayfası) ve varsa CrUX origin verisi. Hiçbir güvenlik check'i çalışmaz.

"Desen kopyalanır, içerik kopyalanmaz": anatomi örnek sitelerin metnini taşımaz. Başlık ve h1
yerine uzunlukları, CTA metni yerine sadece varlığı tutulur (spec şemasındaki `title`, `h1`,
`primary_cta_text`, `target_phrases` bu yüzden sayıya indirildi; decisions P9).
"""

from typing import Any

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold
from launchcheck.deep.funnel.analysis import has_phrase, pick_signup, pricing_info

ANATOMY_VERSION = 1
SECTION_TYPES = (
    "hero",
    "logos",
    "testimonials",
    "pricing",
    "faq",
    "how_it_works",
    "integrations",
    "features",
    "final_cta",
)
# Sınıf adlarında (İngilizce yaygın) bölüm türü ipuçları
CLASS_HINTS = {
    "testimonials": ("testimonial", "review", "quote"),
    "logos": ("logo", "client", "partner", "brand"),
    "pricing": ("pricing", "price", "plan"),
    "faq": ("faq", "accordion"),
    "features": ("feature", "benefit"),
    "final_cta": ("cta",),
}
MIN_LOGOS = 4
MIN_QUESTIONS = 3


def _keyword(path: str, text: str) -> bool:
    return bool(text) and keywords.has(path, text)


def classify_section(raw: dict[str, Any], index: int, last: bool) -> str:
    heading = str(raw.get("heading") or "")
    classes = str(raw.get("classes") or "")

    def hinted(kind: str) -> bool:
        return _keyword(f"benchmark.sections.{kind}", heading) or any(
            h in classes for h in CLASS_HINTS.get(kind, ())
        )

    if raw.get("h1") or (index == 0 and "hero" in classes):
        return "hero"
    words = int(raw.get("words") or 0)
    if int(raw.get("logo_like") or 0) >= MIN_LOGOS and (words < 80 or hinted("logos")):
        return "logos"
    if (
        int(raw.get("quotes") or 0) >= 2
        or int(raw.get("stars") or 0) >= 3
        or hinted("testimonials")
    ):
        return "testimonials"
    if int(raw.get("prices") or 0) >= 2 or hinted("pricing"):
        return "pricing"
    if (
        int(raw.get("details") or 0) >= MIN_QUESTIONS
        or int(raw.get("questions") or 0) >= MIN_QUESTIONS
        or hinted("faq")
    ):
        return "faq"
    if hinted("how_it_works"):
        return "how_it_works"
    if hinted("integrations"):
        return "integrations"
    if hinted("features") or (int(raw.get("images") or 0) >= 3 and words >= 40):
        return "features"
    if int(raw.get("buttons") or 0) >= 1 and (hinted("final_cta") or (last and words < 80)):
        return "final_cta"
    return "unknown"


def page_inventory(links: list[str]) -> list[str]:
    """Sitedeki sayfa türleri (iç link adreslerinden)."""
    types = keywords.sections("benchmark.page_types")
    found: list[str] = []
    for link in links:
        if link in ("mailto:", "tel:"):
            continue
        path = link.split("://", 1)[-1].split("/", 1)[-1]
        text = fold(path).replace("/", " ").replace("-", " ").replace("_", " ")
        for kind in types:
            if kind not in found and _keyword(f"benchmark.page_types.{kind}", text):
                found.append(kind)
    return found


def _trust(home: dict[str, Any], sections: list[str], inventory: list[str]) -> dict[str, Any]:
    raw = home.get("sections") or []
    quotes = sum(
        int(s.get("quotes") or 0)
        for s, kind in zip(raw, sections, strict=False)
        if kind == "testimonials"
    )
    claims = " | ".join(str(c) for c in home.get("claims") or [])
    footer = str(home.get("footer_text") or "")
    alts = " | ".join(str(a) for a in home.get("alts") or [])
    links = home.get("links") or []
    return {
        "logo_band": "logos" in sections,
        "testimonials": quotes or (1 if "testimonials" in sections else 0),
        "user_count_claim": _keyword("benchmark.user_claims", claims),
        "security_badges": _keyword("benchmark.trust_badges", f"{footer} | {alts}"),
        "company_info": _keyword("benchmark.company_info", footer),
        "contact": "contact" in inventory or "mailto:" in links or "tel:" in links,
        "case_studies": "customers" in inventory,
    }


def _pricing(
    home_inspect: dict[str, Any] | None,
    pricing_inspect: dict[str, Any] | None,
    pricing_clicks: int | None,
) -> dict[str, Any]:
    on_home = bool(home_inspect and home_inspect.get("prices"))
    info = pricing_info("/pricing", pricing_inspect) if pricing_inspect else None
    source = (
        info
        if info is not None and info.prices
        else (pricing_info("/", home_inspect) if on_home and home_inspect else None)
    )
    contexts = fold(
        " | ".join(
            str(c) for c in (pricing_inspect or home_inspect or {}).get("priceContexts") or []
        )
    )
    clicks = 0 if on_home else (pricing_clicks if info is not None and info.prices else None)
    return {
        "visible_on_home": on_home,
        "clicks_to_price": clicks,
        "plans": source.prices if source else 0,
        "free_tier": bool(source and source.free_tier),
        "annual_discount": bool(
            source and "yearly" in source.periods and has_phrase("benchmark.discount", contexts)
        ),
        "highlighted_plan": bool(has_phrase("benchmark.highlight", contexts)),
        "currency": source.currencies[0] if source and source.currencies else None,
    }


def _signup(signup_url: str | None, inspect: dict[str, Any] | None) -> dict[str, Any] | None:
    if not signup_url or not inspect:
        return None
    form = pick_signup(signup_url, inspect)
    if form is None:
        return None
    return {
        "fields": len(form.data_fields),
        "required": form.required_count,
        "social_login": form.social,
        "card_required": form.card,
    }


def build_anatomy(
    home: dict[str, Any],
    *,
    home_inspect: dict[str, Any] | None = None,
    pricing_inspect: dict[str, Any] | None = None,
    pricing_clicks: int | None = None,
    signup_url: str | None = None,
    signup_inspect: dict[str, Any] | None = None,
    field_lcp_ms: float | None = None,
) -> dict[str, Any]:
    raw_sections = [s for s in home.get("sections") or [] if isinstance(s, dict)]
    sections = [
        classify_section(s, i, i == len(raw_sections) - 1) for i, s in enumerate(raw_sections)
    ]
    ordered: list[str] = []
    for kind in sections:
        if kind != "unknown" and (not ordered or ordered[-1] != kind):
            ordered.append(kind)
    inventory = page_inventory([str(x) for x in home.get("links") or []])
    hero = home.get("hero") or {}
    perf = home.get("perf") or {}
    seo = home.get("seo") or {}
    og_w, og_h = [*(home.get("og_size") or []), None, None][:2]
    return {
        "version": ANATOMY_VERSION,
        "hero": {
            "headline_words": int(hero.get("headline_words") or 0),
            "subheadline": bool(hero.get("subheadline")),
            "has_primary_cta": bool(hero.get("primary_cta")),
            "cta_count_above_fold": int(hero.get("cta_above_fold") or 0),
            "media_type": str(hero.get("media") or "none"),
        },
        "sections": ordered,
        "trust": _trust(home, sections, inventory),
        "pricing": _pricing(home_inspect, pricing_inspect, pricing_clicks),
        "signup": _signup(signup_url, signup_inspect),
        "page_inventory": inventory,
        "seo": {
            "title_length": int(seo.get("title_length") or 0),
            "h1_words": int(seo.get("h1_words") or 0),
            "h2_count": int(seo.get("h2_count") or 0),
            "word_count_home": int(seo.get("word_count") or 0),
        },
        "perf": {
            "lcp_ms": perf.get("lcp_ms"),
            "cls": perf.get("cls"),
            "js_kb": perf.get("js_kb"),
            "field_lcp_ms": field_lcp_ms,
            "field_data": field_lcp_ms is not None,
        },
        "share": {
            "og_image": bool(home.get("og_image")),
            "og_image_ratio_ok": None
            if not (og_w and og_h)
            else 1.0 <= int(og_w) / int(og_h) <= 2.2,
        },
        "stack": [str(s) for s in home.get("stack") or []][:6],
    }
