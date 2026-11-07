"""HTML içerik check'leri için ortak yardımcılar (metin sadeleştirme, konum, onay tespiti)."""

import re
from collections.abc import Iterator
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.web._common import page_tree
from launchcheck.collectors.web_collector import PageSnapshot, WebContext

_TR_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇâÂîÎûÛ", "iissgguuooccaaiiuu")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
CONSENT_KEYWORDS = (
    "kvkk",
    "aydinlatma",
    "gizlilik",
    "privacy",
    "acik riza",
    "consent",
    "kisisel veri",
)
# Onay arandığında formdan kaç seviye yukarı çıkılır (AGENTS: form + 3 üst kapsayıcı)
CONSENT_ANCESTOR_LEVELS = 3


def fold(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir: 'Adınız' → 'adiniz'."""
    return text.translate(_TR_FOLD).lower()


def tokens(text: str) -> list[str]:
    """camelCase ve ayraçlardan bölünmüş, sadeleştirilmiş kelimeler."""
    return [t for t in _TOKEN_SPLIT.split(fold(_CAMEL.sub(" ", text))) if t]


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
