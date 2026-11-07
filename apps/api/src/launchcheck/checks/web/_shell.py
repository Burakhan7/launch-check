"""İç sayfanın sunucudan kendi içeriği yerine ana sayfanın HTML'ini dönmesi (SPA fallback).

Tespit check'ler arasında paylaşılır: `web.render.route_serves_root_shell` raporlar; aynı kök
sorunun ikinci kez sayılmaması için empty_server_html, duplicate_title_description ve og_missing
bu sayfaları atlar (`covered_by`).
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.shared.text import fold, squash
from launchcheck.checks.web._common import page_tree
from launchcheck.collectors.web_collector import PageSnapshot, WebContext
from launchcheck.config import get_settings

SHELL_CHECK_ID = "web.render.route_serves_root_shell"
CACHE_KEY = "root_shell_pages"
INVISIBLE_SELECTORS = ("script", "style", "noscript", "template", "svg")
CHROME_SELECTORS = (
    "header",
    "nav",
    "footer",
    "aside",
    '[role="navigation"]',
    '[role="banner"]',
    '[role="contentinfo"]',
)
WORD_RE = re.compile(r"\w+", re.UNICODE)
SHINGLE = 3
MIN_SHINGLE_WORDS = 20  # daha kısa içeriklerde benzerlik anlamlı değil


@dataclass
class ShellPage:
    url: str
    signals: list[str] = field(default_factory=list)


def main_words(html: str) -> list[str]:
    """nav/header/footer/aside ve görünmez öğeler çıkarılmış ana içerik kelimeleri."""
    tree = LexborHTMLParser(html)
    for selector in (*INVISIBLE_SELECTORS, *CHROME_SELECTORS):
        for node in tree.css(selector):
            node.decompose()
    body = tree.body
    text = body.text(deep=True, separator=" ") if body is not None else ""
    return WORD_RE.findall(fold(text))


def similarity(a: list[str], b: list[str]) -> float | None:
    """Kelime 3-shingle Jaccard benzerliği; içerik çok kısaysa None."""
    if len(a) < MIN_SHINGLE_WORDS or len(b) < MIN_SHINGLE_WORDS:
        return None
    sa = {tuple(a[i : i + SHINGLE]) for i in range(len(a) - SHINGLE + 1)}
    sb = {tuple(b[i : i + SHINGLE]) for i in range(len(b) - SHINGLE + 1)}
    return len(sa & sb) / len(sa | sb)


def _first_text(tree: LexborHTMLParser, selector: str) -> str:
    node = tree.css_first(selector)
    return fold(squash(node.text(deep=True, separator=" "))) if node is not None else ""


def _is_root(url: str) -> bool:
    return urlsplit(url).path in ("", "/")


def canonical_to_root(page: PageSnapshot, tree: LexborHTMLParser) -> bool:
    node = tree.css_first('link[rel="canonical"]')
    href = (node.attributes.get("href") or "") if node is not None else ""
    if not href or _is_root(page.url):
        return False
    return _is_root(urljoin(page.url, href))


def shell_signals(
    ctx: WebContext, page: PageSnapshot, home: PageSnapshot, home_words: list[str]
) -> list[str]:
    tree = page_tree(ctx, page)
    home_tree = page_tree(ctx, home)
    signals = []
    if canonical_to_root(page, tree):
        signals.append("canonical_to_root")
    title = _first_text(tree, "title")
    if (
        title
        and title == _first_text(home_tree, "title")
        and (_first_text(tree, "h1") == _first_text(home_tree, "h1"))
    ):
        signals.append("same_title_h1")
    score = similarity(main_words(page.html), home_words)
    if score is not None and score >= get_settings().web_shell_similarity_threshold:
        signals.append(f"content_similarity_{score:.2f}")
    return signals


def root_shell_pages(ctx: WebContext) -> list[ShellPage]:
    """Ana sayfanın kopyasını dönen iç sayfalar (en az `web_shell_min_signals` sinyal).
    Önbellekli: birden fazla check aynı sonucu kullanır."""
    if CACHE_KEY not in ctx.cache:
        ctx.cache[CACHE_KEY] = _compute(ctx)
    pages: list[ShellPage] = ctx.cache[CACHE_KEY]
    return pages


def _compute(ctx: WebContext) -> list[ShellPage]:
    if len(ctx.pages) < 2:  # tek sayfalık site
        return []
    home = ctx.pages[0]
    home_words = main_words(home.html)
    minimum = get_settings().web_shell_min_signals
    out = []
    for page in ctx.pages[1:]:
        if page.url.rstrip("/") == home.url.rstrip("/"):
            continue
        signals = shell_signals(ctx, page, home, home_words)
        if len(signals) >= minimum:
            out.append(ShellPage(page.url, signals))
    return out


def covered_urls(ctx: WebContext) -> set[str]:
    return {p.url for p in root_shell_pages(ctx)}
