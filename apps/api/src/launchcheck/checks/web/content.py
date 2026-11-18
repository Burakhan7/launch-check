"""Canlı sitede herkese görünen test/deneme verisi ve tahmin edilebilir (sıralı) kaynak ID'leri.

Pasif: sadece taranan sayfalardaki başlık/link metinleri ve iç link adresleri okunur.
Bulunan ID'lere istek ATILMAZ (yetki testi Faz 4'tedir).
"""

import asyncio
import hashlib
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.shared.text import fold, phrase_regex, squash
from launchcheck.checks.web._common import WebCheck, is_html, meta_content, page_tree
from launchcheck.checks.web._html import ancestor, attr, iter_pages, location_of
from launchcheck.checks.web._shell import CHROME_SELECTORS, INVISIBLE_SELECTORS, covered_urls
from launchcheck.checks.web.meta import social_meta
from launchcheck.checks.web.render import covered_note
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


# --- Framework / AI aracı şablonundan kalanlar
# Listeler gerçek şablonlardan doğrulandı (bkz. docs/decisions.md, 2026-11-18).

# Ana içerikte (küçük harf, boşluklar teke inmiş) geçerse şablon sayfası
TEMPLATE_PHRASES = (
    "edit src/app",
    "and save to test",  # Vite: Edit <code>src/App.tsx</code> and save to test <code>HMR</code>
    "and save to reload",  # create-react-app
    "click on the vite and",  # Vite <= 6
    "get started by editing",  # create-next-app <= 15
    "to get started, edit the",  # create-next-app 16
    "welcome to your lovable project",
    "welcome to your blank app",  # Lovable boş proje
    "start building your amazing project here",  # Lovable boş proje
    "start prompting (or editing) to see magic happen",  # Bolt
)
# Sadece Vite/React logosu veya linki de varsa sayılır (tek başına çok genel)
LOGO_GATED_PHRASES = ("count is", "learn react")
# Sunucu HTML'i boş SPA'larda (Vite) metin JS bundle'ındadır; derlenmiş JSX'te string
# literal'lar <code> sınırlarında bölünür, bu yüzden bölünmeyen parçalar aranır.
BUNDLE_TEMPLATE_PHRASES = (
    " and save to test ",
    " and save to reload",
    "Click on the Vite and",
    "Get started by editing",
    "To get started, edit the",
    "Welcome to your Lovable project",
    "Welcome to Your Blank App",
    "Start building your amazing project here",
    "Start prompting (or editing) to see magic happen",
)
FRAMEWORK_LOGO_RE = re.compile(r"vite|react", re.IGNORECASE)
FRAMEWORK_LINK_RE = re.compile(r"vite\.dev|vitejs\.dev|react\.dev|reactjs\.org", re.IGNORECASE)
CODE_TAGS = "code, kbd, samp"
DEFAULT_TITLES = frozenset(
    {
        # create-vite 9.3.4 şablonlarının tamamı + spec listesi
        "vite + react + ts",
        "vite + react",
        "vite + vue",
        "vite + vue + ts",
        "vite + svelte",
        "vite + svelte + ts",
        "vite + preact",
        "vite + preact + ts",
        "vite + solid",
        "vite + solid + ts",
        "vite + lit",
        "vite + lit + ts",
        "vite + qwik",
        "vite + qwik + ts",
        "vite + js",
        "vite + ts",
        "vite app",
        "react app",  # create-react-app
        "create next app",  # create-next-app 16
        "next.js app",
        "lovable app",
        "vite_react_shadcn_ts",  # Lovable
        "my app",
        "untitled",
        "document",
    }
)
DEFAULT_DESCRIPTIONS = (
    "generated by create next app",
    "web site created using create-react-app",
    "lovable generated project",
    "a vite + react project",
)
TOOL_AUTHORS = frozenset({"lovable"})
TOOL_TWITTER_SITES = frozenset({"@lovable_dev"})
TOOL_IMAGE_DOMAINS = frozenset({"lovable.dev", "lovable.app", "bolt.new", "vercel.com", "v0.dev"})
DEFAULT_ICON_SUFFIXES = ("/vite.svg", "/next.svg", "/vercel.svg")
# SHA-256 → kaynak şablon (npm paketlerinden hesaplandı; bkz. decisions.md)
KNOWN_DEFAULT_FAVICON_HASHES = {
    "c28fdd2a4f31e2dc64f653962286da5c82a4cdfc518b242d32812c624e9a19a4": "create-next-app@16.4.6",
    "3d10f7da6c603178340081668c4ac5b3ae9743ca9a262ab0fcd312fbb9f48bdd": "cra-template@1.3.0",
    "61bc9a161de58248288e6905425d7180f0624c2865007b97d763fdac12043a66": "create-vite@9.3.4",
    "ceeac38434be7a3b4d0f68b8cd8aa2b9ae78c260d6343087c6e095f8031ce4ff": "create-vite@9.3.4",
}
HASHED_ICON_NAMES = ("/favicon.ico", "/favicon.svg")
MAX_HASHED_ICONS = 2
# Sitenin kendisi aracın/framework'ün sitesiyse check çalışmaz
TOOL_SITE_DOMAINS = frozenset(
    {"lovable.dev", "vitejs.dev", "vite.dev", "nextjs.org", "react.dev", "vercel.com", "bolt.new"}
)
LEFTOVER_SEVERITY = {
    "template_page": Severity.HIGH,
    "default_title": Severity.MEDIUM,
    "default_description": Severity.MEDIUM,
    "tool_branding_meta": Severity.LOW,
    "tool_og_image": Severity.LOW,
    "default_favicon": Severity.LOW,
}
MAX_VALUE_CHARS = 120


def _template_text(html: str) -> tuple[str, int]:
    """Ana içerik metni (nav/header/footer ve görünmez öğeler hariç; empty_server_html ile aynı
    çıkarım), küçük harf. `pre` blokları ve şablon cümlesini tek başına taşıyan code/kbd/samp
    öğeleri çıkarılır: şablonu anlatan blog yazısı yakalanmasın. (metin, kelime sayısı)"""
    tree = LexborHTMLParser(html)
    for selector in (*INVISIBLE_SELECTORS, *CHROME_SELECTORS, "pre"):
        for node in tree.css(selector):
            node.decompose()
    for node in tree.css(CODE_TAGS):
        own = squash(node.text(deep=True, separator=" ")).lower()
        if any(p in own for p in (*TEMPLATE_PHRASES, *LOGO_GATED_PHRASES)):
            node.decompose()
    body = tree.body
    text = squash(body.text(deep=True, separator=" ")).lower() if body is not None else ""
    return text, len(text.split())


def _has_framework_logo(tree: LexborHTMLParser) -> bool:
    for img in tree.css("img"):
        if FRAMEWORK_LOGO_RE.search(f"{attr(img, 'alt')} {attr(img, 'src')}"):
            return True
    return any(FRAMEWORK_LINK_RE.search(attr(a, "href")) for a in tree.css("a[href]"))


def _template_phrases(ctx: WebContext) -> list[tuple[str, str]]:
    """(cümle, kaynak) — kaynak "html" veya JS bundle'ı (boş SPA kabuğunda)."""
    home = ctx.pages[0]
    text, words = _template_text(home.html)
    found = [(p, "html") for p in TEMPLATE_PHRASES if p in text]
    if _has_framework_logo(page_tree(ctx, home)):
        found += [(p, "html") for p in LOGO_GATED_PHRASES if p in text]
    if found or words >= get_settings().web_min_main_content_words:
        return found
    for script in ctx.scripts:
        if not script.inline and origin_of(script.url) != ctx.origin:
            continue
        for phrase in BUNDLE_TEMPLATE_PHRASES:
            if phrase in script.content and (phrase.strip(), "js") not in found:
                found.append((phrase.strip(), "js"))
    return found


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def _home_meta_items(ctx: WebContext) -> list[dict[str, str]]:
    home = ctx.pages[0]
    tree = page_tree(ctx, home)
    page = _path(home.url)
    items: list[dict[str, str]] = []
    phrases = _template_phrases(ctx)
    if phrases:  # tek sayfa = tek item; eşleşen cümleler value'da
        item = {"kind": "template_page", "page": page, "value": " | ".join(p for p, _ in phrases)}
        if all(src == "js" for _, src in phrases):
            item["source"] = "js"
        items.append(item)
    title_node = tree.css_first("title")
    title = squash(title_node.text(deep=True)) if title_node is not None else ""
    if title.lower() in DEFAULT_TITLES:
        items.append({"kind": "default_title", "page": page, "value": title})
    social = social_meta(tree)
    for desc in dict.fromkeys(
        d for d in (meta_content(tree, name="description"), social.get("og:description", "")) if d
    ):
        lower = desc.lower()
        if any(lower == d or d in lower for d in DEFAULT_DESCRIPTIONS):
            items.append({"kind": "default_description", "page": page, "value": desc})
            break
    author = meta_content(tree, name="author")
    twitter_site = social.get("twitter:site", "")
    if author.lower() in TOOL_AUTHORS:
        items.append({"kind": "tool_branding_meta", "page": page, "value": f"author={author}"})
    elif twitter_site.lower() in TOOL_TWITTER_SITES:
        items.append(
            {"kind": "tool_branding_meta", "page": page, "value": f"twitter:site={twitter_site}"}
        )
    site_host = urlsplit(home.url).hostname or ""
    for key in ("og:image", "twitter:image"):
        host = urlsplit(urljoin(home.url, social.get(key, ""))).hostname or ""
        if host and host != site_host and registered_domain(host) in TOOL_IMAGE_DOMAINS:
            items.append({"kind": "tool_og_image", "page": page, "value": social[key]})
            break
    return items


def _icon_hrefs(ctx: WebContext, page_url: str, tree: LexborHTMLParser) -> list[str]:
    hrefs = []
    for node in tree.css("link[rel][href]"):
        if "icon" in attr(node, "rel").lower():
            url = urljoin(page_url, attr(node, "href"))
            if origin_of(url) == ctx.origin:
                hrefs.append(_path(url))
    return hrefs


async def _favicon_items(ctx: WebContext, covered: set[str]) -> list[dict[str, str]]:
    items = []
    hashable: list[str] = []
    for page, tree in iter_pages(ctx):
        if page.url in covered:
            continue
        for href in _icon_hrefs(ctx, page.url, tree):
            if href.lower().endswith(DEFAULT_ICON_SUFFIXES):
                items.append({"kind": "default_favicon", "page": _path(page.url), "value": href})
            elif href.lower().endswith(HASHED_ICON_NAMES) and href not in hashable:
                hashable.append(href)
    if "/favicon.ico" not in hashable:
        hashable.insert(0, "/favicon.ico")
    results = await asyncio.gather(*(ctx.real_file(p) for p in hashable[:MAX_HASHED_ICONS]))
    for path, res in zip(hashable, results, strict=False):
        if res is None or is_html(res):
            continue
        if hashlib.sha256(res.body).hexdigest() in KNOWN_DEFAULT_FAVICON_HASHES:
            items.append({"kind": "default_favicon", "page": "/", "value": path})
    return items


def _dedupe(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Aynı kind + value farklı sayfalarda tekrarlanıyorsa tek item, `pages` sayısıyla."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        value = item["value"][:MAX_VALUE_CHARS]
        key = (item["kind"], value)
        if key not in merged:
            merged[key] = {**item, "value": value, "_pages": {item["page"]}}
        else:
            merged[key]["_pages"].add(item["page"])
    out = []
    for entry in merged.values():
        pages = entry.pop("_pages")
        if len(pages) > 1:
            entry["pages"] = len(pages)
        out.append(entry)
    return out


@register
class FrameworkLeftovers(WebCheck):
    """Framework/AI aracı şablonundan kalan başlık, açıklama, görsel, ikon ve sayfa içeriği."""

    id = "web.content.framework_leftovers"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        if not ctx.pages:
            return []
        host = urlsplit(ctx.final_url).hostname or ""
        if registered_domain(host) in TOOL_SITE_DOMAINS:
            return []  # aracın kendi sitesi
        covered = covered_urls(ctx)
        items = _dedupe([*_home_meta_items(ctx), *await _favicon_items(ctx, covered)])
        if not items:
            return []
        severity = min((LEFTOVER_SEVERITY[i["kind"]] for i in items), key=lambda s: s.rank)
        return [self.finding({"items": items, **covered_note(covered)}, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        kinds = dict.fromkeys(str(i.get("kind")) for i in evidence.get("items") or [])
        return {"kinds": ", ".join(fragment(k) for k in kinds)}
