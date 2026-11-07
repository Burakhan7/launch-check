"""Menü/footer'da link gibi görünüp çalışmayan öğeler ve sayfalar arasında tutarsız footer'lar.

Pasif: linklere istek atılmaz; sadece taranan sayfaların HTML yapısı karşılaştırılır.
"""

import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, squash
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import attr, iter_pages, location_of
from launchcheck.collectors.web_collector import WebContext

MAX_ITEMS = 10
MAX_DEAD_WORDS = 3  # link listesindeki link olmayan kısa metin (1-3 kelime)
MIN_LINK_SIBLINGS = 2
# Açılır menü/modal tetikleyicisi: href="#" olsa da çalışır
TRIGGER_ATTRS = (
    "aria-expanded",
    "aria-haspopup",
    "aria-controls",
    "data-toggle",
    "data-bs-toggle",
    "onclick",
)
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# Telif/adres satırları link listesinde düz metin olarak durur; sorun değildir
NON_ITEM_RE = re.compile(r"©|\(c\)|\b(?:19|20)\d{2}\b|@")
CHROME = ("nav", "footer")
HEADING_CLASS_RE = re.compile(r"title|heading|baslik|header|label", re.IGNORECASE)


def _in_footer(node: LexborNode) -> bool:
    """Footer içindeki nav'lar da footer sayılır (`location_of` önce nav'ı bulur)."""
    current = node.parent
    while current is not None and current.tag not in ("body", "html"):
        if current.tag == "footer" or attr(current, "role") == "contentinfo":
            return True
        current = current.parent
    return False


def _location(node: LexborNode) -> str:
    return "footer" if _in_footer(node) else location_of(node)


def _is_dead_anchor(node: LexborNode) -> bool:
    if any(node.attributes.get(a) is not None for a in TRIGGER_ATTRS):
        return False
    if attr(node, "role") == "button":
        return False
    href = (node.attributes.get("href") or "").strip()
    if node.attributes.get("href") is None:
        return True
    return href in ("", "#") or href.lower().startswith("javascript:")


def _short_label(text: str) -> bool:
    words = text.split()
    return (
        0 < len(words) <= MAX_DEAD_WORDS
        and bool(LETTER_RE.search(text))
        and not NON_ITEM_RE.search(text)
    )


def _has_link(node: LexborNode) -> bool:
    return node.tag == "a" or node.css_first("a[href], button") is not None


# Sayfa linki gibi duran düğmeler (çerez tercihleri düğmesi meşrudur, ayar penceresi açar)
PAGE_LIKE_CATEGORIES = ("privacy", "terms", "rules", "contact", "support", "refund")


def _page_like_buttons(tree: LexborHTMLParser) -> list[LexborNode]:
    """Link grubunda (en az 2 link kardeşi), metni bir sayfa linki gibi duran (İletişim,
    Destek, Gizlilik...) ama link olmayan ve açılır menü/modal tetikleyicisi olmayan düğmeler."""
    found = []
    for button in tree.css("button"):
        if any(button.attributes.get(a) is not None for a in TRIGGER_ATTRS):
            continue
        if attr(button, "type").lower() == "submit" or button.parent is None:
            continue
        siblings = [c for c in button.parent.iter(include_text=False) if c.tag == "a"]
        if len(siblings) < MIN_LINK_SIBLINGS:
            continue
        text = fold(squash(button.text(deep=True, separator=" ")))
        if any(keywords.has(f"footer_links.{c}", text) for c in PAGE_LIKE_CATEGORIES):
            found.append(button)
    return found


def _plain_items_among_links(tree: LexborHTMLParser) -> list[LexborNode]:
    """Kardeşleri link olan ama kendisi link olmayan kısa metin öğeleri (li veya link grubu)."""
    found: list[LexborNode] = []
    for parent in tree.css("ul, ol, div, p"):
        children = [c for c in parent.iter(include_text=False)]
        if len(children) < MIN_LINK_SIBLINGS + 1:
            continue
        linked = [c for c in children if _has_link(c)]
        if len(linked) < MIN_LINK_SIBLINGS:
            continue
        for position, child in enumerate(children):
            if _has_link(child) or child.css_first("input, select, img, svg") is not None:
                continue
            # Grubun ilk öğesi veya başlık class'lı öğe sütun başlığıdır ("Kurumsal")
            if position == 0 or HEADING_CLASS_RE.search(attr(child, "class")):
                continue
            if child.tag not in ("li", "span", "p", "div"):
                continue
            if _short_label(squash(child.text(deep=True, separator=" "))):
                found.append(child)
    return found


def dead_items(tree: LexborHTMLParser) -> list[tuple[str, str]]:
    """(metin, konum) — sadece nav/header/footer içinde."""
    out: list[tuple[str, str]] = []
    candidates = [a for a in tree.css("a") if _is_dead_anchor(a)]
    candidates += _plain_items_among_links(tree)
    candidates += _page_like_buttons(tree)
    for node in candidates:
        location = _location(node)
        if location not in CHROME:
            continue
        text = squash(node.text(deep=True, separator=" "))
        if text and _short_label(text) and (text, location) not in out:
            out.append((text, location))
    return out


def footer_links(tree: LexborHTMLParser) -> dict[str, str]:
    """Footer'daki yasal/destek linkleri: kategori → link metni."""
    categories = keywords.sections("footer_links")
    found: dict[str, str] = {}
    for node in tree.css("a[href]"):
        if not _in_footer(node) or _is_dead_anchor(node):
            continue
        text = squash(node.text(deep=True, separator=" "))
        haystack = fold(f"{text} {attr(node, 'href').replace('-', ' ').replace('/', ' ')}")
        for name in categories:
            if name not in found and keywords.has(f"footer_links.{name}", haystack):
                found[name] = text or attr(node, "href")
    return found


def _has_footer(tree: LexborHTMLParser) -> bool:
    return tree.css_first('footer, [role="contentinfo"]') is not None


@register
class DeadOrInconsistentNav(WebCheck):
    id = "web.links.dead_or_inconsistent_nav"
    category = Category.CONVERSION
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        dead: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        home_links: dict[str, str] | None = None
        others: dict[str, str] = {}
        for index, (page, tree) in enumerate(iter_pages(ctx)):
            for text, location in dead_items(tree):
                if (text, location) not in seen:  # her sayfada tekrar eden footer tek sayılır
                    seen.add((text, location))
                    dead.append({"page": page.url, "text": text, "location": location})
            if not _has_footer(tree):
                continue
            links = footer_links(tree)
            if index == 0:
                home_links = links
            else:
                for name, text in links.items():
                    others.setdefault(name, text)
        # Ana sayfada ölü öğe olarak zaten raporlanan kategoriler (ör. düz metin "İletişim")
        # eksik link listesinde tekrar edilmez.
        dead_home = {
            name
            for d in dead
            if d["page"] == (ctx.pages[0].url if ctx.pages else "")
            for name in keywords.sections("footer_links")
            if keywords.has(f"footer_links.{name}", fold(d["text"]))
        }
        missing = (
            [
                text
                for name, text in others.items()
                if name not in home_links and name not in dead_home
            ]
            if home_links is not None
            else []
        )
        if not dead and not missing:
            return []
        contact_dead = any(keywords.has("contact_items", fold(d["text"])) for d in dead)
        severity = Severity.MEDIUM if (missing or contact_dead) else Severity.LOW
        evidence: dict[str, Any] = {
            "dead_items": dead[:MAX_ITEMS],
            "missing_on_homepage": missing[:MAX_ITEMS],
        }
        return [self.finding(evidence, severity=severity)]
