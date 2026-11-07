"""Canlı sitede herkese görünen test/deneme verisi ve tahmin edilebilir (sıralı) kaynak ID'leri.

Pasif: sadece taranan sayfalardaki başlık/link metinleri ve iç link adresleri okunur.
Bulunan ID'lere istek ATILMAZ (yetki testi Faz 4'tedir).
"""

from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.text import fold, phrase_regex, squash
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import ancestor, attr, iter_pages, location_of
from launchcheck.collectors.web_assets import origin_of
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_MATCHES = 10
MAX_TITLE_CHARS = 60
MEDIUM_MATCHES = 3
TITLE_SELECTORS = (
    "title, h1, h2, h3, h4, h5, h6, a, li, strong, a > span, a > div, li > span,"
    " [class*=title], [class*=name], [class*=baslik]"
)


@register
class TestDataInProduction(WebCheck):
    __test__ = False  # pytest bunu test sınıfı sanmasın
    id = "web.content.test_data_in_production"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        keyword_re = phrase_regex(settings.web_test_data_keywords)
        exact = set(settings.web_test_data_exact_only)
        exceptions = settings.web_test_data_exceptions
        matches: dict[str, dict[str, str]] = {}
        for page, tree in iter_pages(ctx):
            for node in tree.css(TITLE_SELECTORS):
                if node.tag != "title" and location_of(node) != "content":
                    continue  # menü/footer linkleri sayılmaz
                # Sadece öğenin kendi metni: iç içe öğeler (li > a > span) tek kez sayılır,
                # rozet gibi alt öğeler ("Şifreli") başlığa karışmaz.
                text = squash(node.text(deep=False))
                if not text or len(text) > MAX_TITLE_CHARS:
                    continue
                folded = fold(text)
                if folded in matches or any(e in folded for e in exceptions):
                    continue
                if folded in exact or keyword_re.search(folded):
                    matches[folded] = {"text": text, "page": page.url}
        if not matches:
            return []
        severity = Severity.MEDIUM if len(matches) >= MEDIUM_MATCHES else Severity.LOW
        evidence = {"matches": list(matches.values())[:MAX_MATCHES], "count": len(matches)}
        return [self.finding(evidence, severity=severity)]


MAX_PATTERNS = 5
MAX_SAMPLE_IDS = 5
MAX_PAGES = 5
PRIVATE_RE = phrase_regex(("sifreli", "ozel", "private", "locked", "kilitli"), prefix=True)
LOCK_EMOJI = "\U0001f512"
LIST_ANCESTOR_LEVELS = 2


def _id_patterns(url: str, names: set[str], max_value: int) -> list[tuple[str, int]]:
    """URL'deki /<hassas-segment>/<sayı> ve ?<hassas-param>=<sayı> desenleri."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    found = []
    for i, segment in enumerate(segments[:-1]):
        nxt = segments[i + 1]
        if fold(unquote(segment)) in names and nxt.isdigit() and int(nxt) < max_value:
            found.append(("/" + "/".join(segments[: i + 1]) + "/{id}", int(nxt)))
    for key, value in parse_qsl(parts.query):
        if fold(key) in names and value.isdigit() and int(value) < max_value:
            found.append((f"{parts.path or '/'}?{key}={{id}}", int(value)))
    return found


def _looks_private(text: str) -> bool:
    return LOCK_EMOJI in text or bool(PRIVATE_RE.search(fold(text)))


@register
class SequentialPublicIds(WebCheck):
    id = "web.ids.sequential_public_ids"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        names = {fold(n) for n in settings.web_sequential_id_names}
        ids: dict[str, set[int]] = defaultdict(set)
        pages: dict[str, list[str]] = defaultdict(list)
        private: dict[str, bool] = defaultdict(bool)
        for page, tree in iter_pages(ctx):
            for node in tree.css("a[href]"):
                url = urljoin(page.url, attr(node, "href"))
                if origin_of(url) != ctx.origin:
                    continue
                for pattern, value in _id_patterns(
                    url, names, settings.web_sequential_id_max_value
                ):
                    ids[pattern].add(value)
                    if page.url not in pages[pattern]:
                        pages[pattern].append(page.url)
                    container = ancestor(node, LIST_ANCESTOR_LEVELS)
                    if _looks_private(container.text(deep=True, separator=" ")):
                        private[pattern] = True
        reported = [p for p, v in ids.items() if len(v) >= settings.web_sequential_id_min_distinct]
        if not reported:
            return []
        private_listed = any(private[p] for p in reported)
        evidence: dict[str, Any] = {
            "patterns": [
                {
                    "pattern": p,
                    "sample_ids": sorted(ids[p])[:MAX_SAMPLE_IDS],
                    "pages": pages[p][:MAX_PAGES],
                }
                for p in reported[:MAX_PATTERNS]
            ],
            "private_items_listed": private_listed,
        }
        return [
            self.finding(evidence, severity=Severity.MEDIUM if private_listed else Severity.LOW)
        ]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        private = fragment("private_listed") if evidence.get("private_items_listed") else ""
        return {"private_note": private}
