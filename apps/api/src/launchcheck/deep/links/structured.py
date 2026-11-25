"""JSON-LD doğrulama ve yapılandırılmış veri fırsatları (PHASE2 §6.4).

Zorunlu alanlar Google'ın zengin sonuç belgelerindeki "required" alanlardır (2026-11 itibarıyla
sadeleştirilmiş). Sadece en yaygın türler kontrol edilir; bilinmeyen türler hata sayılmaz.
Fırsat tespiti tek bir kelimeye dayanmaz: sayfa yapısı (fiyat metni sayısı, soru başlıkları,
telefon/adres/harita) + dile özgü kelime listeleri birlikte aranır.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold
from launchcheck.deep.links_data import JsonLdIssue, JsonLdPage, SdOpportunity
from launchcheck.deep.render_data import SdSignals

MAX_ISSUES = 10
WRAPPER_RE = re.compile(r"^\s*(?://\s*)?<!\[CDATA\[|(?://\s*)?\]\]>\s*$|^\s*<!--|-->\s*$")
# Yerel işletme alt türleri: listede olmayanlar ad sonuna bakılarak tanınır ("...Store")
LOCAL_TYPES = frozenset(
    {
        "LocalBusiness",
        "Restaurant",
        "FoodEstablishment",
        "CafeOrCoffeeShop",
        "Bakery",
        "BarOrPub",
        "Dentist",
        "MedicalBusiness",
        "MedicalClinic",
        "Physician",
        "Hotel",
        "LodgingBusiness",
        "AutoRepair",
        "AutomotiveBusiness",
        "BeautySalon",
        "HairSalon",
        "DaySpa",
        "HealthAndBeautyBusiness",
        "ProfessionalService",
        "RealEstateAgent",
        "LegalService",
        "Attorney",
        "HomeAndConstructionBusiness",
        "SportsActivityLocation",
        "ExerciseGym",
        "ChildCare",
        "EducationalOrganization",
    }
)
LOCAL_SUFFIXES = ("Store", "Business", "Shop")
PRODUCT_TYPES = frozenset({"Product", "ProductGroup", "Offer", "AggregateOffer", "Car", "Book"})
FAQ_TYPES = frozenset({"FAQPage", "QAPage"})
ARTICLE_TYPES = frozenset({"Article", "BlogPosting", "NewsArticle", "TechArticle", "Report"})


def _types(node: dict[str, Any]) -> list[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    out = []
    for value in values:
        if isinstance(value, str) and value:
            out.append(value.rsplit("/", 1)[-1].rsplit(":", 1)[-1])  # schema:Product, URL
    return out


def is_local(type_name: str) -> bool:
    return type_name in LOCAL_TYPES or type_name.endswith(LOCAL_SUFFIXES)


def _has(node: dict[str, Any], key: str) -> bool:
    value = node.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict):
        return bool(value)
    return True


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _missing(node: dict[str, Any], type_name: str, *fields: str) -> list[JsonLdIssue]:
    return [JsonLdIssue("missing_field", type_name, f) for f in fields if not _has(node, f)]


def _offer_issues(offer: Any, type_name: str) -> list[JsonLdIssue]:
    if not isinstance(offer, dict):
        return []
    kinds = _types(offer)
    if "AggregateOffer" in kinds:
        out = _missing(offer, "AggregateOffer", "lowPrice", "priceCurrency")
    elif _has(offer, "priceSpecification"):
        out = []
    else:
        out = _missing(offer, "Offer", "price", "priceCurrency")
    return [JsonLdIssue(i.kind, i.type, i.field, f"{type_name}.offers") for i in out]


def _product(node: dict[str, Any], name: str) -> list[JsonLdIssue]:
    issues = _missing(node, name, "name")
    if not any(_has(node, k) for k in ("offers", "review", "aggregateRating")):
        issues.append(JsonLdIssue("missing_field", name, "offers|review|aggregateRating"))
    for offer in _as_list(node.get("offers"))[:5]:
        issues += _offer_issues(offer, name)
    return issues


def _faq(node: dict[str, Any], name: str) -> list[JsonLdIssue]:
    entities = [e for e in _as_list(node.get("mainEntity")) if isinstance(e, dict)]
    if not entities:
        return [JsonLdIssue("missing_field", name, "mainEntity")]
    issues: list[JsonLdIssue] = []
    for question in entities[:20]:
        issues += _missing(question, "Question", "name")
        answers = _as_list(question.get("acceptedAnswer")) or _as_list(
            question.get("suggestedAnswer")
        )
        if not any(isinstance(a, dict) and _has(a, "text") for a in answers):
            issues.append(JsonLdIssue("missing_field", "Question", "acceptedAnswer.text"))
    return issues


def _breadcrumbs(node: dict[str, Any], name: str) -> list[JsonLdIssue]:
    items = [i for i in _as_list(node.get("itemListElement")) if isinstance(i, dict)]
    if not items:
        return [JsonLdIssue("missing_field", name, "itemListElement")]
    issues: list[JsonLdIssue] = []
    for item in items[:20]:
        issues += _missing(item, "ListItem", "position")
        named = _has(item, "name") or (
            isinstance(item.get("item"), dict) and _has(item["item"], "name")
        )
        if not named:
            issues.append(JsonLdIssue("missing_field", "ListItem", "name"))
    return issues


def _rating(node: dict[str, Any], name: str) -> list[JsonLdIssue]:
    issues = _missing(node, name, "ratingValue")
    if not (_has(node, "ratingCount") or _has(node, "reviewCount")):
        issues.append(JsonLdIssue("missing_field", name, "ratingCount|reviewCount"))
    return issues


def node_issues(node: dict[str, Any]) -> list[JsonLdIssue]:
    """Tek bir düğümün türüne göre zorunlu alan kontrolü. Bilinmeyen tür → kontrol yok."""
    issues: list[JsonLdIssue] = []
    for name in _types(node):
        if name in ("Product", "ProductGroup"):
            issues += _product(node, name)
        elif name in FAQ_TYPES:
            issues += _faq(node, name)
        elif name == "BreadcrumbList":
            issues += _breadcrumbs(node, name)
        elif name in ARTICLE_TYPES:
            issues += _missing(node, name, "headline")
        elif name == "Event":
            issues += _missing(node, name, "name", "startDate", "location")
        elif name == "Recipe":
            issues += _missing(node, name, "name", "image")
        elif name == "JobPosting":
            issues += _missing(
                node, name, "title", "datePosted", "description", "hiringOrganization"
            )
        elif name == "AggregateRating":
            issues += _rating(node, name)
        elif is_local(name):
            issues += _missing(node, name, "name", "address")
        elif name == "Organization":
            issues += _missing(node, name, "name")
    if _has(node, "aggregateRating") and isinstance(node.get("aggregateRating"), dict):
        issues += _rating(node["aggregateRating"], "AggregateRating")
    return issues


def _nodes(data: Any) -> Iterator[dict[str, Any]]:
    """Üst düzey düğümler ve `@graph` elemanları."""
    for item in _as_list(data):
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        if isinstance(graph, list):
            for child in graph:
                if isinstance(child, dict):
                    yield child
            if not _types(item):
                continue
        yield item


def _context_ok(data: Any) -> bool:
    for item in _as_list(data):
        if not isinstance(item, dict):
            continue
        context = item.get("@context")
        text = json.dumps(context) if context is not None else ""
        if "schema.org" not in text:
            return False
    return True


def clean_block(text: str) -> str:
    """CDATA/HTML yorum sarmalayıcılarını kaldırır (bazı CMS'ler ekler; tarayıcılar ve Google
    bunları yok sayar, JSON ayrıştırıcısı saymaz)."""
    lines = [WRAPPER_RE.sub("", line) for line in text.strip().splitlines()]
    return "\n".join(lines).strip()


def analyze_blocks(page: str, blocks: list[str]) -> JsonLdPage:
    result = JsonLdPage(page=page, blocks=len(blocks))
    for raw in blocks:
        text = clean_block(raw)
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError as exc:
            line = getattr(exc, "lineno", 0)
            col = getattr(exc, "colno", 0)
            result.issues.append(JsonLdIssue("parse_error", detail=f"{line}:{col}"))
            continue
        if not _context_ok(data):
            result.issues.append(JsonLdIssue("missing_context"))
        for node in _nodes(data):
            names = _types(node)
            if not names:
                result.issues.append(JsonLdIssue("missing_type"))
                continue
            for name in names:
                if name not in result.types:
                    result.types.append(name)
            result.issues += node_issues(node)
    result.issues = list(dict.fromkeys(result.issues))[:MAX_ISSUES]
    return result


def microdata_types(itemtypes: list[str]) -> list[str]:
    out = []
    for raw in itemtypes:
        for part in raw.split():
            name = part.rstrip("/").rsplit("/", 1)[-1]
            if name and name not in out:
                out.append(name)
    return out


# --- fırsatlar


def _any_keyword(path: str, texts: list[str]) -> bool:
    return any(keywords.has(path, fold(t)) for t in texts)


def opportunities(page: str, signals: SdSignals, site_types: set[str]) -> list[SdOpportunity]:
    """Sayfada türün sinyalleri var ama sitede (herhangi bir sayfada) o tür yapılandırılmış veri
    hiç yok. Her tür en az iki bağımsız sinyal ister."""
    out: list[SdOpportunity] = []
    has_product = bool(site_types & PRODUCT_TYPES)
    if (
        not has_product
        and signals.prices >= 1
        and _any_keyword("structured_data.add_to_cart", signals.actions)
    ):
        out.append(SdOpportunity("product", page, [f"prices:{signals.prices}", "add_to_cart"]))
    has_faq = bool(site_types & FAQ_TYPES)
    faq_heading = _any_keyword("structured_data.faq_headings", signals.headings)
    if not has_faq and signals.questions >= 3 and (faq_heading or signals.accordions >= 3):
        sig = [f"questions:{signals.questions}"]
        sig.append("faq_heading" if faq_heading else f"accordions:{signals.accordions}")
        out.append(SdOpportunity("faq", page, sig))
    # Footer'ında telefon ve adres olan her şirket yerel işletme değildir (SaaS): harita gömmesi
    # şart. Organization verisi olan site zaten kendini tanıtıyor; ayrıca önerilmez.
    has_local = any(is_local(t) for t in site_types) or "Organization" in site_types
    local = [
        name
        for name, ok in (
            ("map", signals.map),
            ("tel", signals.tel > 0),
            ("address", signals.address),
        )
        if ok
    ]
    if not has_local and signals.map and len(local) >= 2:
        out.append(SdOpportunity("local_business", page, local))
    return out
