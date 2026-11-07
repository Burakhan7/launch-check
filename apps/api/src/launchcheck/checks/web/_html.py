"""HTML içerik check'leri için ortak yardımcılar (metin sadeleştirme, konum, onay tespiti)."""

import asyncio
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.shared.text import fold
from launchcheck.checks.shared.text import tokens as tokens  # yeniden dışa aktarım
from launchcheck.checks.web._common import page_tree
from launchcheck.collectors.web_assets import origin_of
from launchcheck.collectors.web_collector import (
    CONTENT_SIGNALS_KEY,
    HTTP_OK,
    PageSnapshot,
    WebContext,
)
from launchcheck.collectors.web_signals import ContentSignals, analyze_content, visible_text
from launchcheck.config import get_settings

CONSENT_KEYWORDS = (
    "kvkk",
    "aydinlatma",
    "gizlilik",
    "privacy",
    "acik riza",
    "consent",
    "kisisel veri",
)
EXTRA_PAGES_KEY = "extra_info_pages"
# Footer'daki bu linkler ek sayfa olarak çekilir (yaş sınırı / kriz kaynakları aranırken)
INFO_PAGE_KEYWORDS = (
    "hakkinda",
    "hakkimizda",
    "about",
    "sartlar",
    "kosullar",
    "kullanim",
    "terms",
    "tos",
    "kural",
    "rules",
    "guidelines",
    "yardim",
    "help",
    "sss",
    "faq",
    "destek",
    "support",
    "guvenlik",
    "safety",
)
# Onay arandığında formdan kaç seviye yukarı çıkılır (AGENTS: form + 3 üst kapsayıcı)
CONSENT_ANCESTOR_LEVELS = 3


def iter_pages(ctx: WebContext) -> Iterator[tuple[PageSnapshot, LexborHTMLParser]]:
    pages = ctx.pages or []
    for page in pages:
        yield page, page_tree(ctx, page)


def attr(node: LexborNode, name: str) -> str:
    value: Any = node.attributes.get(name)
    return str(value) if value else ""


def ancestor(node: LexborNode, levels: int) -> LexborNode:
    current = node
    for _ in range(levels):
        parent = current.parent
        if parent is None or parent.tag in ("html", "#document", None):
            break
        current = parent
    return current


def location_of(node: LexborNode) -> str:
    """Linkin sayfadaki yeri: nav (nav/header içinde), footer veya content."""
    current = node.parent
    while current is not None and current.tag not in ("body", "html"):
        role = attr(current, "role")
        if current.tag in ("nav", "header") or role in ("navigation", "banner"):
            return "nav"
        if current.tag == "footer" or role == "contentinfo":
            return "footer"
        current = current.parent
    return "content"


def _has_consent_text(container: LexborNode) -> bool:
    texts = [container.text(deep=True, separator=" ")]
    texts += [attr(a, "href") for a in container.css("a[href]")]
    haystack = fold(" ".join(texts))
    return any(keyword in haystack for keyword in CONSENT_KEYWORDS)


def has_consent_near(node: LexborNode) -> bool:
    """Form (veya düğüm) ve en fazla 3 üst kapsayıcısında: onay kutusu + KVKK/gizlilik metni."""
    container = ancestor(node, CONSENT_ANCESTOR_LEVELS)
    return bool(container.css('input[type="checkbox"]')) and _has_consent_text(container)


def page_has_consent(tree: LexborHTMLParser) -> bool:
    """Sayfanın herhangi bir yerinde KVKK/gizlilik metinli bir onay kutusu var mı?"""
    return any(has_consent_near(box) for box in tree.css('input[type="checkbox"]'))


def content_signals(ctx: WebContext) -> ContentSignals:
    """Collector'ın hesapladığı UGC/hassas platform sinyalleri (yoksa burada hesaplanır)."""
    if CONTENT_SIGNALS_KEY not in ctx.cache:
        sources = [*ctx.all_script_urls, *(s.content for s in ctx.scripts)]
        ctx.cache[CONTENT_SIGNALS_KEY] = analyze_content(
            ((p.url, p.html) for p in ctx.pages), sources
        )
    signals: ContentSignals = ctx.cache[CONTENT_SIGNALS_KEY]
    return signals


def page_text(ctx: WebContext, page: PageSnapshot) -> str:
    """Sayfanın sadeleştirilmiş görünen metni (önbellekli)."""
    for signals_page in content_signals(ctx).pages:
        if signals_page.url == page.url:
            return signals_page.text
    key = f"page_text:{page.url}"
    if key not in ctx.cache:
        ctx.cache[key] = visible_text(page.html)
    text: str = ctx.cache[key]
    return text


def _is_info_link(node: LexborNode) -> bool:
    words = tokens(f"{attr(node, 'href')} {node.text(deep=True, separator=' ')}")
    return any(w.startswith(INFO_PAGE_KEYWORDS) for w in words)


def _info_link_candidates(ctx: WebContext) -> list[str]:
    crawled = {p.url.rstrip("/") for p in ctx.pages}
    found: list[str] = []
    for page, tree in iter_pages(ctx):
        for node in tree.css("a[href]"):
            if location_of(node) != "footer" or not _is_info_link(node):
                continue
            url = urljoin(page.url, attr(node, "href")).split("#", 1)[0]
            if origin_of(url) != ctx.origin or url.rstrip("/") in crawled or url in found:
                continue
            found.append(url)
    return found


async def extra_info_pages(ctx: WebContext) -> list[PageSnapshot]:
    """Footer'da linklenen ve taranmamış hakkında/şartlar/yardım/güvenlik sayfaları
    (en fazla `web_extra_info_pages`). SSRF korumalı, önbellekli; check'ler ortak kullanır."""
    if EXTRA_PAGES_KEY not in ctx.cache:
        ctx.cache[EXTRA_PAGES_KEY] = asyncio.ensure_future(_load_info_pages(ctx))
    pages: list[PageSnapshot] = await asyncio.shield(ctx.cache[EXTRA_PAGES_KEY])
    return pages


async def _load_info_pages(ctx: WebContext) -> list[PageSnapshot]:
    limit = get_settings().web_extra_info_pages
    urls = _info_link_candidates(ctx)[:limit]
    results = await asyncio.gather(*(ctx.fetch_url(u, follow_redirects=True) for u in urls))
    pages = []
    for res in results:
        if res is None or res.status != HTTP_OK or "html" not in res.content_type:
            continue
        if origin_of(res.final_url) != ctx.origin:
            continue
        pages.append(PageSnapshot(res.final_url, res.status, res.headers, res.text))
    return pages
