"""Dönüşüm (satış/büyüme) check'leri: kayıt yerine girişe yönlendiren CTA'lar, boş herkese açık
listeler ve anahtar kelime doldurma. Pasif: sadece taranan sayfaların HTML'i okunur.
"""

import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.shared.text import fold, squash, tokens
from launchcheck.checks.web._common import WebCheck, is_noindex, meta_content, page_tree
from launchcheck.checks.web._html import (
    attr,
    content_signals,
    hero_section,
    iter_pages,
    location_of,
)
from launchcheck.checks.web._shell import CHROME_SELECTORS, INVISIBLE_SELECTORS, covered_urls
from launchcheck.collectors.web_collector import PageSnapshot, WebContext
from launchcheck.config import get_settings

# --- web.conversion.cta_targets_login

PROMINENT_CLASS_RE = re.compile(r"btn|button|cta|primary", re.IGNORECASE)
MODE_PARAMS = ("mode", "tab", "view", "action", "screen")
LOGIN_MODES = {"login", "signin", "sign-in", "giris"}
REGISTER_MODES = {"signup", "sign-up", "register", "kayit", "join"}
MIN_SIGNUP_FIELDS = 2  # kayıt formu: 2 şifre alanı veya şifre + en az 2 başka alan
MIN_ITEM_WORDS = 3


def auth_target(url: str) -> str | None:
    """Link hedefi giriş mi kayıt mı? Son yol parçası veya ?mode= değerine göre."""
    parts = urlsplit(url)
    segments = [fold(s) for s in parts.path.split("/") if s]
    last = segments[-1] if segments else ""
    modes = {fold(v) for k, v in parse_qsl(parts.query) if k.lower() in MODE_PARAMS}
    login = {fold(w) for w in keywords.words("auth_targets.login")}
    register_words = {fold(w) for w in keywords.words("auth_targets.register")}
    if last in register_words or modes & REGISTER_MODES:
        return "register"
    if last in login or modes & LOGIN_MODES:
        return "login"
    return None


def _is_prominent(node: LexborNode) -> bool:
    return (
        bool(PROMINENT_CLASS_RE.search(attr(node, "class")))
        or attr(node, "role") == "button"
        or node.css_first("button") is not None
    )


def _contains(container: LexborNode | None, node: LexborNode) -> bool:
    current: LexborNode | None = node
    while container is not None and current is not None:
        if current.mem_id == container.mem_id:
            return True
        current = current.parent
    return False


def _last_section(tree: LexborHTMLParser) -> LexborNode | None:
    sections = [s for s in tree.css("section") if location_of(s) == "content"]
    return sections[-1] if sections else None


def _has_signup_form(ctx: WebContext) -> bool:
    for page, tree in iter_pages(ctx):
        if auth_target(page.url) == "register":
            return True
        for form in tree.css("form"):
            passwords = len(form.css('input[type="password"]'))
            others = [
                n
                for n in form.css("input")
                if attr(n, "type").lower() not in ("password", "hidden", "submit", "checkbox")
            ]
            if passwords >= MIN_SIGNUP_FIELDS or (
                passwords == 1 and len(others) >= MIN_SIGNUP_FIELDS
            ):
                return True
    return False


@register
class CtaTargetsLogin(WebCheck):
    id = "web.conversion.cta_targets_login"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        if not content_signals(ctx).has_login or not ctx.pages:
            return []
        home = ctx.pages[0]
        tree = page_tree(ctx, home)
        hero = hero_section(tree)
        last = _last_section(tree)
        header = {"login": False, "register": False}
        counts = {"login": 0, "register": 0}
        last_cta: dict[str, str] | None = None
        any_register = False
        for node in tree.css("a[href]"):
            target = auth_target(urljoin(home.url, attr(node, "href")))
            if target is None:
                continue
            any_register = any_register or target == "register"
            if location_of(node) == "nav":
                header[target] = True
                continue
            if location_of(node) != "content":
                continue
            if _is_prominent(node) or _contains(hero, node) or _contains(last, node):
                counts[target] += 1
            if last_cta is None and _contains(last, node):
                text = squash(node.text(deep=True, separator=" "))
                last_cta = {"text": text, "target": target}
        evidence: dict[str, Any] = {
            "header_login": header["login"],
            "header_register": header["register"],
            "last_section_cta": last_cta,
            "cta_counts": counts,
        }
        if not any_register and not _has_signup_form(ctx):
            evidence["no_register_path"] = True  # davetli/kapalı beta olabilir
            return [self.finding(evidence, severity=Severity.INFO)]
        total = counts["login"] + counts["register"]
        reasons = [
            name
            for name, hit in (
                ("header_login_only", header["login"] and not header["register"]),
                ("last_cta_login", bool(last_cta and last_cta["target"] == "login")),
                ("most_ctas_login", total >= MIN_SIGNUP_FIELDS and counts["login"] > total / 2),
            )
            if hit
        ]
        if not reasons:
            return []
        evidence["reasons"] = reasons
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        key = "no_register" if evidence.get("no_register_path") else "login"
        return {"headline": fragment(f"headline_{key}"), "situation": fragment(f"situation_{key}")}


# --- web.conversion.empty_public_listing

ITEM_CLASS_RE = re.compile(r"card|item|profile|listing|result|tile|entry|member|server", re.I)
NUMBER_RE = r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+)\s+"
MAX_COUNTERS = 5


def _main_tree(html: str) -> LexborHTMLParser:
    tree = LexborHTMLParser(html)
    for selector in (*INVISIBLE_SELECTORS, *CHROME_SELECTORS):
        for node in tree.css(selector):
            node.decompose()
    return tree


def _signature(node: LexborNode) -> tuple[str, str]:
    return node.tag or "", " ".join(sorted(attr(node, "class").split()))


def _looks_like_item(node: LexborNode) -> bool:
    if node.tag in ("article", "li"):
        return (
            node.css_first("a[href], img") is not None or len(node.text().split()) >= MIN_ITEM_WORDS
        )
    return bool(ITEM_CLASS_RE.search(attr(node, "class"))) and (
        node.css_first("a[href], img") is not None or node.tag == "a"
    )


def listing_item_count(html: str) -> int:
    """Ana içerikteki en büyük tekrarlanan kart/öğe grubu (aynı ebeveyn + aynı tag/class)."""
    tree = _main_tree(html)
    body = tree.body
    if body is None:
        return 0
    groups: Counter[tuple[int, tuple[str, str]]] = Counter()
    for node in body.traverse(include_text=False):
        if node.parent is not None and _looks_like_item(node):
            groups[(node.parent.mem_id, _signature(node))] += 1
    return max(groups.values(), default=0)


def visible_counters(text: str) -> list[tuple[int, str]]:
    """ "12 üye", "0 yorum" gibi sayı + birim ifadeleri. Eşleşme sadeleştirilmiş metinde yapılır,
    etiket özgün metinden alınır (fold karakterleri birebir eşlediği için konumlar aynıdır)."""
    units = keywords.pattern("counter_units")
    if units is None:
        return []
    folded = fold(text)
    source = text if len(folded) == len(text) else folded
    rx = re.compile(NUMBER_RE + "(?:" + units.pattern + r")\w*")
    out = []
    for m in rx.finditer(folded):
        number = int(re.sub(r"[.,]", "", m.group(1)))
        out.append((number, source[m.start() : m.end()]))
    return out


def _own_texts(tree: LexborHTMLParser) -> list[str]:
    body = tree.body
    if body is None:
        return []
    texts = [squash(n.text(deep=False) or "") for n in body.traverse(include_text=False)]
    return [t for t in texts if t]


def is_listing_page(page: PageSnapshot, tree: LexborHTMLParser) -> bool:
    path = " ".join(tokens(urlsplit(page.url).path))
    h1 = tree.css_first("h1")
    heading = fold(squash(h1.text(deep=True, separator=" "))) if h1 is not None else ""
    return keywords.has("listing_pages", path) or keywords.has("listing_pages", heading)


@register
class EmptyPublicListing(WebCheck):
    id = "web.conversion.empty_public_listing"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        entries = []
        worst: Severity | None = None
        for page, tree in iter_pages(ctx):
            # noindex sayfalar (genelde giriş gerektiren üye listeleri) vitrin değildir
            if not is_listing_page(page, tree) or is_noindex(page, tree):
                continue
            main = _main_tree(page.html)
            raw = squash(main.body.text(deep=True, separator=" ")) if main.body else ""
            count = listing_item_count(page.html)
            # Sayaçlar öğe öğe aranır: komşu öğelerin metni birleşince ("Üye 0 Üye 1")
            # sahte sayaç oluşmasın.
            counters = [
                label
                for text in _own_texts(main)
                for n, label in visible_counters(text)
                if n < settings.web_min_social_proof_count
            ]
            empty_state = keywords.has("empty_states", fold(raw))
            levels = []
            if count < settings.web_min_listing_items and (count > 0 or empty_state):
                levels.append(Severity.MEDIUM)
            if counters or empty_state:
                levels.append(Severity.LOW)
            if not levels:
                continue
            level = min(levels, key=lambda s: s.rank)
            worst = level if worst is None or level.rank < worst.rank else worst
            entries.append(
                {
                    "url": page.url,
                    "item_count": count,
                    "visible_counters": counters[:MAX_COUNTERS],
                    "empty_state_text": empty_state,
                }
            )
        if not entries or worst is None:
            return []
        return [self.finding({"pages": entries}, severity=worst)]


# --- web.conversion.keyword_stuffing

APOSTROPHE_RE = re.compile(r"['’`]")
WORD_RE = re.compile(r"\w+", re.UNICODE)
MAX_PAGES = 5
MIN_VARIANT_WORD = 4


BLOCK_SELECTOR = "p, li, dd, td, blockquote, figcaption, h1, h2, h3, h4, h5, h6"
NESTED_BLOCK_TAGS = {"p", "li", "dd", "td", "blockquote"}


def _words(text: str) -> list[str]:
    return WORD_RE.findall(APOSTROPHE_RE.sub("", fold(text)))


def _inside_block(node: LexborNode) -> bool:
    current = node.parent
    while current is not None and current.tag not in ("body", "html"):
        if current.tag in NESTED_BLOCK_TAGS:
            return True
        current = current.parent
    return False


def page_blocks(html: str) -> tuple[list[list[str]], int]:
    """(öbek sayılacak metin blokları, ana içeriğin toplam kelime sayısı).

    Öbekler sadece en az `web_stuffing_min_block_words` kelimelik bloklarda (paragraf, liste
    öğesi, hücre) sayılır: kart açıklamaları, görsel altyazıları ve rozetler gibi kısa,
    şablondan tekrarlanan metinler doldurma değildir. Bloklar arası öbek oluşmaz."""
    tree = _main_tree(html)
    body = tree.body
    if body is None:
        return [], 0
    total = len(_words(body.text(deep=True, separator=" ")))
    minimum = get_settings().web_stuffing_min_block_words
    blocks = []
    for node in tree.css(BLOCK_SELECTOR):
        if _inside_block(node):
            continue
        words = _words(node.text(deep=True, separator=" "))
        if len(words) >= minimum:
            blocks.append(words)
    return blocks, total


def brand_tokens(ctx: WebContext, tree: LexborHTMLParser) -> set[str]:
    name = meta_content(tree, prop="og:site_name")
    host = urlsplit(ctx.final_url).hostname or ""
    label = registered_domain(host).split(".", 1)[0] if host else ""
    return set(WORD_RE.findall(APOSTROPHE_RE.sub("", fold(f"{name} {label}"))))


def stuffed_phrase(blocks: list[list[str]], total: int, brand: set[str]) -> dict[str, Any] | None:
    settings = get_settings()
    if total < settings.web_stuffing_min_words:
        return None
    best: tuple[int, str, int] | None = None
    for n in range(settings.web_stuffing_ngram_min, settings.web_stuffing_ngram_max + 1):
        grams: Counter[tuple[str, ...]] = Counter()
        for words in blocks:
            grams.update(tuple(words[i : i + n]) for i in range(len(words) - n + 1))
        for gram, count in grams.items():
            if count < settings.web_stuffing_min_repeats or set(gram) <= brand:
                continue  # sadece marka adından oluşan öbek sayılmaz
            if count * n / total <= settings.web_stuffing_min_density:
                continue
            score = count * n
            if best is None or score > best[0]:
                best = (score, " ".join(gram), count)
    if best is None:
        return None
    _, phrase, count = best
    density = round(count * len(phrase.split()) / total, 3)
    return {"phrase": phrase, "count": count, "density": density}


def meta_keyword_stats(tree: LexborHTMLParser) -> tuple[int, int]:
    """(giriş sayısı, aynı kelimeyi paylaşan en büyük varyant grubu)."""
    raw = meta_content(tree, name="keywords")
    entries = [fold(e.strip()) for e in raw.split(",") if e.strip()]
    shared: Counter[str] = Counter()
    for entry in entries:
        for word in set(WORD_RE.findall(APOSTROPHE_RE.sub("", entry))):
            if len(word) >= MIN_VARIANT_WORD:
                shared[word] += 1
    return len(entries), max(shared.values(), default=0)


@register
class KeywordStuffing(WebCheck):
    id = "web.conversion.keyword_stuffing"
    category = Category.CONVERSION
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        pages = []
        covered = covered_urls(ctx)  # ana sayfa kopyası dönen sayfalar ayrıca sayılmaz
        for page, tree in iter_pages(ctx):
            if page.url in covered:
                continue
            blocks, total = page_blocks(page.html)
            hit = stuffed_phrase(blocks, total, brand_tokens(ctx, tree))
            if hit:
                pages.append({"url": page.url, **hit})
        if not pages:
            return []
        count, variants = meta_keyword_stats(page_tree(ctx, ctx.pages[0]))
        evidence: dict[str, Any] = {"pages": pages[:MAX_PAGES]}
        if (
            count > settings.web_meta_keywords_max
            or variants >= settings.web_meta_keywords_max_variants
        ):
            evidence["meta_keywords_count"] = count
            evidence["meta_keyword_variants"] = variants
        return [self.finding(evidence)]
