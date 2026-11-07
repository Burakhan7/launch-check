"""Kullanıcılara dağıtılan indirme dosyalarının test/geliştirme sürümü olması.

Pasif: dosyalar İNDİRİLMEZ, HEAD isteği bile atılmaz; sadece link adresi ve metni okunur.
İmza ve sürüm kontrolü Faz 2'dedir.
"""

import re
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from selectolax.lexbor import LexborNode

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, squash
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import ancestor, attr, hero_section, iter_pages
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_LINKS = 10
TOKEN_SPLIT_RE = re.compile(r"[-_./\s]+")
BETA_CONTEXT_LEVELS = 1


def link_text(node: LexborNode) -> str:
    parts = [node.text(deep=True, separator=" "), attr(node, "aria-label"), attr(node, "title")]
    return fold(squash(" ".join(parts)))


def is_app_download(url: str, text: str) -> bool:
    """Uygulama dosyası mı? Arşivler sadece link metni indirme/kurulum diyorsa sayılır."""
    settings = get_settings()
    path = unquote(urlsplit(url).path).lower()
    if path.endswith(tuple(settings.web_download_extensions)):
        return True
    archive = path.endswith(tuple(settings.web_download_archive_extensions))
    return archive and keywords.has("downloads.link_texts", text)


def nonrelease_tokens(url: str) -> list[str]:
    """Dosya adı ve yol parçalarındaki test/dev token'ları (tam token eşleşmesi)."""
    path = fold(unquote(urlsplit(url).path))
    wanted = {t.lower() for t in get_settings().web_nonrelease_tokens}
    return list(dict.fromkeys(t for t in TOKEN_SPLIT_RE.split(path) if t in wanted))


def _inside(node: LexborNode, container: LexborNode | None) -> bool:
    if container is None:
        return False
    current: LexborNode | None = node
    while current is not None:
        if current.mem_id == container.mem_id:
            return True
        current = current.parent
    return False


@register
class NonReleaseBuild(WebCheck):
    id = "web.downloads.non_release_build"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        primary_limit = get_settings().web_primary_download_links
        links: dict[str, dict[str, Any]] = {}
        for index, (page, tree) in enumerate(iter_pages(ctx)):
            hero = hero_section(tree) if index == 0 else None
            downloads_seen = 0
            for node in tree.css("a[href]"):
                url = urljoin(page.url, attr(node, "href")).split("#", 1)[0]
                text = link_text(node)
                if not is_app_download(url, text):
                    continue
                downloads_seen += 1
                tokens = nonrelease_tokens(url)
                if not tokens or url in links:
                    continue
                context = fold(squash(ancestor(node, BETA_CONTEXT_LEVELS).text(deep=True)))
                links[url] = {
                    "url": url,
                    "page": page.url,
                    "tokens": tokens,
                    # Ana sayfada hero'daki veya sıradaki ilk N indirme linki birincil CTA
                    "is_primary_cta": index == 0
                    and (_inside(node, hero) or downloads_seen <= primary_limit),
                    "labeled_as_beta": keywords.has("downloads.beta_labels", f"{text} {context}"),
                }
        if not links:
            return []
        entries = list(links.values())
        severity = min((_severity(e) for e in entries), key=lambda s: s.rank)
        return [
            self.finding({"links": entries[:MAX_LINKS], "count": len(entries)}, severity=severity)
        ]


def _severity(entry: dict[str, Any]) -> Severity:
    if entry["labeled_as_beta"]:
        return Severity.LOW  # kullanıcı bilerek beta indiriyor
    return Severity.HIGH if entry["is_primary_cta"] else Severity.MEDIUM
